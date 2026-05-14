import asyncio
import re
import time
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path

from app.core.config import settings
from app.mcp import ToolDefinition, ToolExecutionResult, tool_registry
from app.providers import ProviderConfigError, ProviderRequestError, TextBlock, ToolUseBlock, create_client
from app.schemas.events import MessageCreate, SessionCreate, SessionSummary, TimelineEvent
from app.schemas.subagents import SubagentRunCreate
from app.schemas.tasks import TaskCreate
from app.schemas.teammates import TeammateCreate
from app.services import approval_service, session_service, subagent_service, teammate_service, tool_service
from app.tools.broker import ToolBroker, broker


@dataclass
class PendingApproval:
    session_id: str
    context: dict[str, object]


@dataclass
class SessionTurn:
    task: asyncio.Task[None]
    cancel_event: asyncio.Event
    partial_text: str = ""


class TurnCancelled(RuntimeError):
    pass


class EventBroker:
    def __init__(self) -> None:
        self._queues: dict[str, list[asyncio.Queue[TimelineEvent]]] = defaultdict(list)

    async def publish(self, event: TimelineEvent) -> None:
        for queue in list(self._queues[event.session_id]):
            await queue.put(event)

    def subscribe(self, session_id: str) -> asyncio.Queue[TimelineEvent]:
        queue: asyncio.Queue[TimelineEvent] = asyncio.Queue()
        self._queues[session_id].append(queue)
        return queue

    def unsubscribe(self, session_id: str, queue: asyncio.Queue[TimelineEvent]) -> None:
        queues = self._queues.get(session_id, [])
        if queue in queues:
            queues.remove(queue)


class RuntimeManager:
    def __init__(self) -> None:
        self.events = EventBroker()
        self.pending_approvals: dict[int, PendingApproval] = {}
        self.background_tasks: set[asyncio.Task[None]] = set()
        self.session_turns: dict[str, SessionTurn] = {}

    def restore_state(self) -> None:
        self.pending_approvals = {}
        for approval_id, session_id, context in approval_service.list_pending_runtime_contexts():
            if not session_id:
                continue
            self.pending_approvals[approval_id] = PendingApproval(
                session_id=session_id,
                context=context,
            )

    def list_sessions(self) -> list[SessionSummary]:
        return session_service.list_sessions()

    async def rename_session(self, session_id: str, title: str) -> SessionSummary | None:
        updated = session_service.update_session_title(session_id, title)
        if updated:
            await self.publish(
                TimelineEvent(
                    session_id=session_id,
                    type="session.renamed",
                    content=title,
                )
            )
        return updated

    async def soft_delete_session(self, session_id: str) -> bool:
        deleted = session_service.soft_delete_session(session_id)
        if deleted:
            self.session_turns.pop(session_id, None)
        return deleted

    def session_exists(self, session_id: str) -> bool:
        return session_service.get_session(session_id) is not None

    def list_timeline(self, session_id: str) -> list[TimelineEvent]:
        return session_service.list_event_records(session_id)

    def list_tool_executions(self, session_id: str | None = None):
        return tool_service.list_tool_executions(session_id)

    def list_approvals(self, session_id: str | None = None):
        return approval_service.list_approvals(session_id)

    def list_teammates(self, session_id: str | None = None):
        return teammate_service.list_teammates(session_id)

    def list_teammate_messages(self, agent_id: int):
        return teammate_service.list_teammate_messages(agent_id)

    def list_subagents(self, session_id: str | None = None):
        return subagent_service.list_subagents(session_id)

    async def decide_approval(self, approval_id: int, approve: bool, feedback: str = ""):
        decision = approval_service.update_approval(approval_id, approve=approve, feedback=feedback)
        if not decision:
            return None
        pending = self.pending_approvals.pop(approval_id, None)
        if decision.session_id:
            await self.publish(
                TimelineEvent(
                    session_id=decision.session_id,
                    type="approval.resolved",
                    content=f"Approval #{approval_id} {decision.status}.",
                )
            )
        if approve and pending:
            reply = await self._resume_agent_loop_after_approval(pending.session_id, pending.context)
            if reply:
                await self._publish_assistant_reply(pending.session_id, reply)
        return decision

    async def create_teammate(self, payload: TeammateCreate):
        teammate = teammate_service.create_teammate(payload)
        await self.publish(
            TimelineEvent(
                session_id=payload.session_id,
                type="teammate.created",
                content=f"Teammate '{payload.name}' ({payload.role}) joined the session.",
            )
        )
        return teammate

    async def send_teammate_message(self, agent_id: int, content: str):
        teammate = teammate_service.get_teammate(agent_id)
        if not teammate:
            return None
        teammate_service.update_teammate_status(agent_id, "working")
        outbound = teammate_service.add_teammate_message(agent_id, "to_agent", content)
        if teammate.session_id:
            await self.publish(
                TimelineEvent(
                    session_id=teammate.session_id,
                    type="teammate.message",
                    content=f"Sent to {teammate.name}: {content}",
                )
            )
        reply_text = self._generate_teammate_reply(teammate.name, teammate.role, content)
        inbound = teammate_service.add_teammate_message(agent_id, "from_agent", reply_text)
        teammate_service.update_teammate_status(agent_id, "idle")
        if teammate.session_id:
            await self.publish(
                TimelineEvent(
                    session_id=teammate.session_id,
                    type="teammate.reply",
                    content=f"{teammate.name}: {reply_text}",
                )
            )
        return {"sent": outbound, "reply": inbound}

    async def run_subagent(self, payload: SubagentRunCreate):
        subagent = subagent_service.create_subagent(payload.session_id, payload.name)
        await self.publish(
            TimelineEvent(
                session_id=payload.session_id,
                type="subagent.started",
                content=f"Subagent '{payload.name}' started.",
            )
        )
        summary = await self._run_subagent_task(payload.session_id, payload.prompt)
        subagent_service.add_subagent_summary(subagent.id, summary)
        completed = subagent_service.complete_subagent(subagent.id)
        await self.publish(
            TimelineEvent(
                session_id=payload.session_id,
                type="subagent.summary",
                content=f"{payload.name}: {summary}",
            )
        )
        return {"subagent": completed or subagent, "summary": summary}

    async def _run_subagent_task(self, session_id: str, prompt: str) -> str:
        workspace = self._resolve_request_workspace(prompt) or settings.project_root
        messages: list[dict[str, object]] = [{"role": "user", "content": prompt}]
        return await self._continue_agent_loop(
            session_id,
            workspace,
            messages,
            asyncio.Event(),
            allow_subagent_tool=False,
            agent_kind="subagent",
            emit_stream_events=False,
        )

    async def publish(self, event: TimelineEvent) -> TimelineEvent:
        stored = session_service.create_event_record(event)
        await self.events.publish(stored)
        return stored

    async def emit_ephemeral(self, event: TimelineEvent) -> TimelineEvent:
        await self.events.publish(event)
        return event

    def _start_background_turn(self, session_id: str, content: str) -> None:
        existing = self.session_turns.get(session_id)
        if existing and not existing.task.done():
            existing.cancel_event.set()
        cancel_event = asyncio.Event()
        task = asyncio.create_task(self._run_lead_turn(session_id, content, cancel_event))
        self.session_turns[session_id] = SessionTurn(task=task, cancel_event=cancel_event)
        self.background_tasks.add(task)
        task.add_done_callback(self.background_tasks.discard)

    async def cancel_session_turn(self, session_id: str) -> bool:
        turn = self.session_turns.get(session_id)
        if turn is None or turn.task.done():
            return False
        turn.cancel_event.set()
        partial_text = turn.partial_text.strip()
        if partial_text:
            session_service.create_message_record(session_id, MessageCreate(role="assistant", content=partial_text))
            await self.publish(
                TimelineEvent(
                    session_id=session_id,
                    type="message.assistant",
                    content=partial_text,
                )
            )
        turn.task.cancel()
        await self.publish(
            TimelineEvent(
                session_id=session_id,
                type="turn.cancelled",
                content="Stopped the current turn.",
            )
        )
        return True

    async def create_session(self, payload: SessionCreate) -> SessionSummary:
        session = session_service.create_session_record(payload)
        await self.publish(
            TimelineEvent(
                session_id=session.session_id,
                type="session.created",
                content=f"Session '{payload.title}' created.",
            )
        )
        return session

    async def append_message(self, session_id: str, payload: MessageCreate) -> None:
        should_autoname = payload.role == "user" and self._should_autoname_session(session_id)
        session_service.create_message_record(session_id, payload)
        await self.publish(
            TimelineEvent(
                session_id=session_id,
                type=f"message.{payload.role}",
                content=payload.content,
            )
        )
        if payload.role == "user":
            if should_autoname:
                self._start_autoname_session(session_id, payload.content)
            self._start_background_turn(session_id, payload.content)

    async def _run_lead_turn(self, session_id: str, content: str, cancel_event: asyncio.Event) -> None:
        await self.emit_ephemeral(
            TimelineEvent(
                session_id=session_id,
                type="runtime.state",
                content="Lead runtime is evaluating the latest user turn.",
            )
        )
        try:
            reply = await self._run_agent_task(session_id, content, cancel_event)
            await self._publish_assistant_reply(session_id, reply)
            return
        except TurnCancelled:
            return
        except asyncio.CancelledError:
            return
        except Exception as exc:
            reply = f"Lead runtime failed: {exc}"
        finally:
            current = self.session_turns.get(session_id)
            if current and current.cancel_event is cancel_event:
                self.session_turns.pop(session_id, None)

        await self._publish_assistant_reply(session_id, reply)

    async def _run_agent_task(self, session_id: str, latest_user_content: str, cancel_event: asyncio.Event) -> str:
        workspace = self._resolve_request_workspace(latest_user_content)
        if workspace is None:
            return "我没有定位到你指定的目标项目路径。请给我更明确的本地项目名或绝对路径。"

        messages: list[dict[str, object]] = session_service.list_message_records(session_id, limit=12)
        return await self._continue_agent_loop(session_id, workspace, messages, cancel_event)

    def _resolve_request_workspace(self, content: str) -> Path | None:
        absolute_match = re.search(r"(/Users/[^\s，。；；!?\n]+)", content)
        if absolute_match:
            candidate = Path(absolute_match.group(1)).expanduser().resolve()
            if candidate.exists() and candidate.is_dir():
                return candidate
            return None

        candidates = [settings.project_root]
        projects = settings.codex_config.get("projects")
        if isinstance(projects, dict):
            for raw_path in projects.keys():
                path = Path(str(raw_path)).expanduser()
                if path.exists() and path.is_dir():
                    candidates.append(path.resolve())

        parent = settings.project_root.parent
        if parent.exists():
            for child in parent.iterdir():
                if child.is_dir():
                    candidates.append(child.resolve())

        normalized = content.lower()
        unique: list[Path] = []
        seen: set[str] = set()
        for path in candidates:
            key = path.as_posix()
            if key in seen:
                continue
            seen.add(key)
            unique.append(path)

        for path in sorted(unique, key=lambda item: len(item.name), reverse=True):
            if path == settings.project_root:
                continue
            if path.name.lower() in normalized:
                return path
        return settings.project_root

    def _autonomous_tool_schemas(self, *, allow_subagent_tool: bool = True) -> list[dict[str, object]]:
        schemas = [
            {
                "name": "list_files",
                "description": "List files in the current target workspace.",
                "input_schema": {"type": "object", "properties": {}},
            },
            {
                "name": "read_file",
                "description": "Read a file from the current target workspace.",
                "input_schema": {
                    "type": "object",
                    "properties": {"path": {"type": "string"}},
                    "required": ["path"],
                },
            },
            {
                "name": "write_file",
                "description": "Create or overwrite a file in the current target workspace.",
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "path": {"type": "string"},
                        "content": {"type": "string"},
                    },
                    "required": ["path", "content"],
                },
            },
            {
                "name": "edit_file",
                "description": "Replace exact text in a file in the current target workspace.",
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "path": {"type": "string"},
                        "old_text": {"type": "string"},
                        "new_text": {"type": "string"},
                    },
                    "required": ["path", "old_text", "new_text"],
                },
            },
            {
                "name": "bash",
                "description": "Run a shell command in the current target workspace. This requires approval before execution.",
                "input_schema": {
                    "type": "object",
                    "properties": {"command": {"type": "string"}},
                    "required": ["command"],
                },
            },
            {
                "name": "list_skills",
                "description": "List locally installed skills available to the agent.",
                "input_schema": {"type": "object", "properties": {}},
            },
            {
                "name": "load_skill",
                "description": "Load a local skill by name and read its SKILL.md instructions.",
                "input_schema": {
                    "type": "object",
                    "properties": {"name": {"type": "string"}},
                    "required": ["name"],
                },
            },
            {
                "name": "create_task",
                "description": "Create a lightweight task in the current session.",
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "subject": {"type": "string"},
                        "description": {"type": "string"},
                    },
                    "required": ["subject"],
                },
            },
            {
                "name": "run_subagent",
                "description": "Delegate a bounded investigation or implementation subtask to a subagent. Use this for complex tasks, long investigations, or independent subproblems. The subagent returns a written summary of what it found or changed.",
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "name": {"type": "string"},
                        "prompt": {"type": "string"},
                    },
                    "required": ["prompt"],
                },
            },
            {
                "name": "create_teammate",
                "description": "Create a teammate agent for the current session.",
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "name": {"type": "string"},
                        "role": {"type": "string"},
                    },
                    "required": ["name", "role"],
                },
            },
            {
                "name": "message_teammate",
                "description": "Send a message to a teammate agent in the current session.",
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "agent_id": {"type": "integer"},
                        "content": {"type": "string"},
                    },
                    "required": ["agent_id", "content"],
                },
            },
        ]
        if not allow_subagent_tool:
            schemas = [schema for schema in schemas if schema["name"] != "run_subagent"]
        return schemas

    async def _autonomous_tool_definitions(self, *, allow_subagent_tool: bool = True) -> list[ToolDefinition]:
        return await tool_registry.list_tools(allow_subagent_tool=allow_subagent_tool)

    def _tool_schemas_from_definitions(self, definitions: list[ToolDefinition]) -> list[dict[str, object]]:
        return [
            {
                "name": definition.name,
                "description": definition.description,
                "input_schema": definition.input_schema,
            }
            for definition in definitions
        ]

    def _should_autoname_session(self, session_id: str) -> bool:
        session = session_service.get_session(session_id)
        if session is None:
            return False
        if not session.title.startswith("New Session") and session.title != "New Session":
            return False
        if session_service.has_user_messages(session_id):
            return False
        return True

    def _start_autoname_session(self, session_id: str, content: str) -> None:
        task = asyncio.create_task(self._autoname_session(session_id, content))
        self.background_tasks.add(task)
        task.add_done_callback(self.background_tasks.discard)

    async def _autoname_session(self, session_id: str, content: str) -> None:
        title = await asyncio.to_thread(self._generate_session_title, content)
        if title:
            updated = session_service.update_session_title(session_id, title)
            if updated:
                await self.publish(
                    TimelineEvent(
                        session_id=session_id,
                        type="session.renamed",
                        content=title,
                    )
                )

    def _generate_session_title(self, content: str) -> str:
        llm_title = self._generate_session_title_with_llm(content)
        if llm_title:
            return llm_title
        return self._summarize_session_title_fallback(content)

    def _generate_session_title_with_llm(self, content: str) -> str | None:
        if not settings.model_id:
            return None
        prompt = (
            "Summarize the user's first message into a concise session title.\n"
            "Return only the title.\n"
            "Keep it under 8 words in English or under 16 Chinese characters.\n"
            "No quotes. No punctuation unless needed.\n"
            "Make it specific to the task."
        )
        try:
            response = create_client().messages.create(
                model=settings.model_id,
                system=prompt,
                messages=[{"role": "user", "content": content[:1200]}],
                max_tokens=32,
            )
        except (ProviderConfigError, ProviderRequestError, Exception):
            return None

        text = " ".join(
            block.text.strip()
            for block in response.content
            if isinstance(block, TextBlock) and block.text.strip()
        ).strip()
        if not text:
            return None
        text = " ".join(text.split()).strip().strip("\"'` ")
        if not text:
            return None
        return text[:120]

    def _summarize_session_title_fallback(self, content: str) -> str:
        text = " ".join(content.strip().split())
        if not text:
            return "New Session"
        for prefix in ("请帮我", "帮我", "麻烦", "请", "能不能", "可以帮我", "can you ", "could you ", "please ", "help me "):
            if text.lower().startswith(prefix.lower()):
                text = text[len(prefix):].strip()
                break
        text = text.strip(" .,!?:;，。！？：；\"'()[]{}")
        if not text:
            return "New Session"
        has_cjk = any("\u4e00" <= char <= "\u9fff" for char in text)
        if has_cjk:
            return text[:14]
        words = text.split()
        return " ".join(words[:5])[:36]

    def _generate_teammate_reply(self, name: str, role: str, content: str) -> str:
        return f"{name} ({role}) acknowledged the request and recommends tackling: {content[:120]}"

    def _generate_subagent_summary(self, prompt: str) -> str:
        files = broker.run("list_files", {})[1].splitlines()[:8]
        summary_parts = [f"Prompt: {prompt[:140]}"]
        if files:
            summary_parts.append("Workspace sample:\n" + "\n".join(files))
        if "readme" in prompt.lower() or "workspace" in prompt.lower() or "project" in prompt.lower():
            status, content = broker.run("read_file", {"path": "README.md"})
            if status == "completed":
                summary_parts.append("README snapshot:\n" + content[:500])
        return "\n\n".join(summary_parts)

    def _skill_roots(self) -> list[Path]:
        roots = [
            settings.project_root / "skills",
            settings.project_root / ".agents" / "skills",
        ]
        return [root for root in roots if root.exists() and root.is_dir()]

    def _list_skills(self) -> str:
        entries: list[str] = []
        for root in self._skill_roots():
            for skill_file in sorted(root.glob("*/SKILL.md")):
                entries.append(f"{skill_file.parent.name} ({skill_file.parent.relative_to(settings.project_root)})")
        unique = sorted(dict.fromkeys(entries))
        return "\n".join(unique[:120]) if unique else "(no skills found)"

    def list_local_skills(self) -> list[dict[str, str]]:
        skills: list[dict[str, str]] = []
        for root in self._skill_roots():
            for skill_file in sorted(root.glob("*/SKILL.md")):
                skills.append(
                    {
                        "name": skill_file.parent.name,
                        "path": skill_file.parent.relative_to(settings.project_root).as_posix(),
                    }
                )
        seen: set[tuple[str, str]] = set()
        unique: list[dict[str, str]] = []
        for skill in skills:
            key = (skill["name"], skill["path"])
            if key in seen:
                continue
            seen.add(key)
            unique.append(skill)
        return unique

    def _load_skill(self, name: str) -> tuple[str, str]:
        requested = name.strip().lower()
        if not requested:
            return "error", "load_skill requires a skill name."

        matches: list[Path] = []
        for root in self._skill_roots():
            for skill_file in root.glob("*/SKILL.md"):
                skill_name = skill_file.parent.name.lower()
                if requested == skill_name or requested in skill_name:
                    matches.append(skill_file)

        if not matches:
            return "error", f"Skill not found: {name}"

        skill_file = sorted(matches)[0]
        return "completed", skill_file.read_text()[:12000]

    async def _execute_autonomous_tool(
        self,
        *,
        session_id: str,
        tool_name: str,
        tool_input: dict[str, object],
        broker_for_workspace: ToolBroker,
    ) -> tuple[str, str] | None:
        if tool_name == "bash":
            return None
        if tool_name in {"list_files", "read_file", "write_file", "edit_file"}:
            return broker_for_workspace.run(tool_name, tool_input)
        if tool_name == "list_skills":
            return "completed", self._list_skills()
        if tool_name == "load_skill":
            return self._load_skill(str(tool_input.get("name", "")))
        if tool_name == "create_task":
            subject = str(tool_input.get("subject", "")).strip()
            description = str(tool_input.get("description", "")).strip()
            if not subject:
                return "error", "create_task requires a non-empty subject."
            task = task_service.create_task(
                TaskCreate(session_id=session_id, subject=subject, description=description)
            )
            await self.publish(
                TimelineEvent(
                    session_id=session_id,
                    type="task.created",
                    content=f"Task '{task.subject}' created.",
                )
            )
            return "completed", f"Created task #{task.id}: {task.subject}"
        if tool_name == "run_subagent":
            prompt = str(tool_input.get("prompt", "")).strip()
            name = str(tool_input.get("name", "")).strip() or f"Explorer {len(self.list_subagents(session_id)) + 1}"
            if not prompt:
                return "error", "run_subagent requires a non-empty prompt."
            result = await self.run_subagent(SubagentRunCreate(session_id=session_id, name=name, prompt=prompt))
            return "completed", result["summary"]
        if tool_name == "create_teammate":
            name = str(tool_input.get("name", "")).strip()
            role = str(tool_input.get("role", "")).strip()
            if not name or not role:
                return "error", "create_teammate requires both name and role."
            teammate = await self.create_teammate(TeammateCreate(session_id=session_id, name=name, role=role))
            return "completed", f"Created teammate #{teammate.id}: {teammate.name} ({teammate.role})"
        if tool_name == "message_teammate":
            agent_id = tool_input.get("agent_id")
            content = str(tool_input.get("content", "")).strip()
            if not isinstance(agent_id, int):
                return "error", "message_teammate requires an integer agent_id."
            if not content:
                return "error", "message_teammate requires non-empty content."
            result = await self.send_teammate_message(agent_id, content)
            if result is None:
                return "error", f"Unknown teammate #{agent_id}"
            return "completed", result["reply"].content
        return "error", f"Unknown tool '{tool_name}'"

    async def _execute_tool_definition(
        self,
        *,
        session_id: str,
        tool: ToolDefinition,
        tool_input: dict[str, object],
        broker_for_workspace: ToolBroker,
    ) -> ToolExecutionResult | None:
        if tool.name == "bash":
            return None
        if tool.source == "mcp":
            return await tool_registry.call_tool(tool, tool_input)
        status, output = await self._execute_autonomous_tool(
            session_id=session_id,
            tool_name=tool.name,
            tool_input=tool_input,
            broker_for_workspace=broker_for_workspace,
        ) or ("error", f"Tool '{tool.name}' returned no execution result.")
        return ToolExecutionResult(status=status, output=output)

    async def _publish_assistant_reply(self, session_id: str, reply: str) -> None:
        text = reply.strip() or "LLM provider returned no text output."
        for chunk in self._chunk_text(text):
            await self.emit_ephemeral(
                TimelineEvent(
                    session_id=session_id,
                    type="message.assistant.delta",
                    content=chunk,
                )
            )
            await asyncio.sleep(0.035)
        session_service.create_message_record(session_id, MessageCreate(role="assistant", content=text))
        await self.publish(
            TimelineEvent(
                session_id=session_id,
                type="message.assistant",
                content=text,
            )
        )

    def _build_agent_system_prompt(self, workspace: Path) -> str:
        return "\n\n".join(
            [
                "You are Jarvis, a local desktop coding agent.",
                f"Target workspace: {workspace}",
                "You may answer directly when no tool is needed, but you should decide for yourself whether tools are necessary.",
                "When the user asks about files, directories, paths, project structure, README contents, code contents, or workspace state, inspect the workspace with tools first instead of guessing.",
                "When the user asks you to create or modify files, do the work directly inside the target workspace when it is safe.",
                "For complex tasks, large investigations, or multiple mostly-independent subproblems, you should proactively use run_subagent to delegate bounded side work and then integrate the result.",
                "If the user explicitly writes command-like instructions such as `read ...`, `write ...`, `edit ...`, or `bash: ...`, treat them as direct tool intents.",
                "Do not ask the user to type explicit tool commands such as read or bash just because you need workspace facts.",
                "Use bash only when necessary; bash requires approval before execution.",
                "After using tools, answer the user's request directly.",
                "Do not mention that you used tools, inspected files, checked the workspace, or can continue using tools unless the user explicitly asks about your method or asks for next steps.",
                "For read-only questions, give the result directly instead of writing a work-log style summary.",
                "Do not append extra offers such as 'if you want I can continue...' unless the user asked for options or follow-up help.",
            ]
        )

    def _build_subagent_system_prompt(self, workspace: Path) -> str:
        return "\n\n".join(
            [
                "You are Jarvis running as a bounded subagent.",
                f"Target workspace: {workspace}",
                "You may use tools to inspect or modify the workspace when needed.",
                "You must not spawn subagents.",
                "You should be concise and execution-focused.",
                "Return a useful summary of what you found or changed. Keep it focused on the outcome, key evidence, and any remaining blocker.",
            ]
        )

    async def _stream_agent_response(
        self,
        *,
        client,
        session_id: str,
        system_prompt: str,
        messages: list[dict[str, object]],
        tools: list[dict[str, object]],
        cancel_event: asyncio.Event,
        emit_stream_events: bool,
    ) -> list[object] | None:
        loop = asyncio.get_running_loop()
        queue: asyncio.Queue[tuple[str, object | None]] = asyncio.Queue()

        def worker() -> None:
            try:
                for event in client.stream_response(
                    model=settings.model_id,
                    system=system_prompt,
                    messages=messages,
                    tools=tools,
                    max_tokens=settings.llm_max_tokens,
                ):
                    if cancel_event.is_set():
                        break
                    asyncio.run_coroutine_threadsafe(queue.put((str(event.get("type")), event)), loop).result()
            except Exception as exc:
                asyncio.run_coroutine_threadsafe(queue.put(("error", str(exc))), loop).result()
            finally:
                asyncio.run_coroutine_threadsafe(queue.put(("done", None)), loop).result()

        worker_task = asyncio.create_task(asyncio.to_thread(worker))
        text_parts: list[str] = []
        tool_blocks: list[object] = []

        try:
            while True:
                if cancel_event.is_set():
                    raise TurnCancelled
                try:
                    kind, payload = await asyncio.wait_for(queue.get(), timeout=0.1)
                except asyncio.TimeoutError:
                    continue
                if kind == "text_delta" and isinstance(payload, dict):
                    delta = str(payload.get("delta") or "")
                    if delta:
                        text_parts.append(delta)
                        if emit_stream_events:
                            turn = self.session_turns.get(session_id)
                            if turn and turn.cancel_event is cancel_event:
                                turn.partial_text += delta
                            await self.emit_ephemeral(
                                TimelineEvent(
                                    session_id=session_id,
                                    type="message.assistant.delta",
                                    content=delta,
                                )
                            )
                    continue
                if kind == "tool_use" and isinstance(payload, dict):
                    tool_blocks.append(
                        ToolUseBlock(
                            id=str(payload.get("id") or ""),
                            name=str(payload.get("name") or ""),
                            input=dict(payload.get("input", {}) or {}),
                        )
                    )
                    continue
                if kind == "error":
                    return [TextBlock(text=str(payload or "LLM provider request failed."))]
                if kind == "done":
                    break
        finally:
            if not cancel_event.is_set():
                await worker_task

        blocks: list[object] = []
        if text_parts:
            blocks.append(TextBlock(text="".join(text_parts)))
        blocks.extend(tool_blocks)
        return blocks

    async def _continue_agent_loop(
        self,
        session_id: str,
        workspace: Path,
        messages: list[dict[str, object]],
        cancel_event: asyncio.Event,
        *,
        allow_subagent_tool: bool = True,
        agent_kind: str = "lead",
        emit_stream_events: bool = True,
    ) -> str:
        client = create_client()
        tool_definitions = await self._autonomous_tool_definitions(allow_subagent_tool=allow_subagent_tool)
        tools = self._tool_schemas_from_definitions(tool_definitions)
        tool_map = {tool.name: tool for tool in tool_definitions}
        broker_for_workspace = ToolBroker(workspace)
        system_prompt = (
            self._build_subagent_system_prompt(workspace)
            if agent_kind == "subagent"
            else self._build_agent_system_prompt(workspace)
        )

        iteration_limit = (
            settings.jarvis_subagent_iteration_limit
            if agent_kind == "subagent"
            else settings.jarvis_agent_iteration_limit
        )

        for _ in range(iteration_limit):
            if cancel_event.is_set():
                raise TurnCancelled
            streamed_blocks = await self._stream_agent_response(
                client=client,
                session_id=session_id,
                system_prompt=system_prompt,
                messages=messages,
                tools=tools,
                cancel_event=cancel_event,
                emit_stream_events=emit_stream_events,
            )
            if streamed_blocks is None:
                return "任务执行失败：模型没有返回可用响应。"
            messages.append({"role": "assistant", "content": self._serialize_content_blocks(streamed_blocks)})
            tool_calls = [block for block in streamed_blocks if getattr(block, "type", "") == "tool_use"]
            if not tool_calls:
                text_blocks = [
                    block.text.strip()
                    for block in streamed_blocks
                    if isinstance(block, TextBlock) and block.text.strip()
                ]
                return "\n\n".join(text_blocks) if text_blocks else "任务已执行，但模型没有返回最终文本说明。"

            results: list[dict[str, str]] = []
            for block in tool_calls:
                tool_definition = tool_map.get(block.name)
                if tool_definition is None:
                    output = f"Unknown tool '{block.name}'"
                    tool_service.create_tool_execution(
                        session_id=session_id,
                        tool_name=block.name,
                        tool_source="local",
                        server_name=None,
                        status="error",
                        input_json=broker_for_workspace.serialize_input(block.input),
                        output_text=output,
                    )
                    results.append(
                        {
                            "type": "tool_result",
                            "tool_use_id": block.id,
                            "content": output,
                        }
                    )
                    continue

                if tool_definition.name == "bash":
                    return await self._queue_bash_approval(
                        session_id,
                        workspace,
                        messages,
                        block.id,
                        block.input,
                        allow_subagent_tool=allow_subagent_tool,
                        agent_kind=agent_kind,
                        emit_stream_events=emit_stream_events,
                    )

                started_at = time.perf_counter()
                executed = await self._execute_tool_definition(
                    session_id=session_id,
                    tool=tool_definition,
                    tool_input=block.input,
                    broker_for_workspace=broker_for_workspace,
                )
                if executed is None:
                    return await self._queue_bash_approval(session_id, workspace, messages, block.id, block.input)
                latency_ms = int((time.perf_counter() - started_at) * 1000)
                tool_service.create_tool_execution(
                    session_id=session_id,
                    tool_name=tool_definition.name,
                    tool_source=tool_definition.source,
                    server_name=tool_definition.server_name,
                    status=executed.status,
                    input_json=broker_for_workspace.serialize_input(block.input),
                    output_text=executed.output,
                    latency_ms=latency_ms,
                    remote_request_id=executed.remote_request_id,
                )
                await self.publish(
                    TimelineEvent(
                        session_id=session_id,
                        type="tool.execution",
                        content=f"{tool_definition.name} -> {executed.status}",
                    )
                )
                results.append(
                    {
                        "type": "tool_result",
                        "tool_use_id": block.id,
                        "content": executed.output,
                    }
                )
            messages.append({"role": "user", "content": results})

        return f"任务执行达到了安全迭代上限（{iteration_limit} 轮），我先停在这里。你可以让我继续，或告诉我希望收敛到哪一步。"

    async def _queue_bash_approval(
        self,
        session_id: str,
        workspace: Path,
        messages: list[dict[str, object]],
        tool_use_id: str,
        tool_input: dict[str, object],
        *,
        allow_subagent_tool: bool,
        agent_kind: str,
        emit_stream_events: bool,
    ) -> str:
        broker_for_workspace = ToolBroker(workspace)
        context = {
            "mode": "agent_loop",
            "workspace": workspace.as_posix(),
            "messages": messages,
            "tool_use_id": tool_use_id,
            "tool_name": "bash",
            "tool_input": tool_input,
            "allow_subagent_tool": allow_subagent_tool,
            "agent_kind": agent_kind,
            "emit_stream_events": emit_stream_events,
        }
        approval = approval_service.create_approval(
            session_id=session_id,
            approval_type="bash",
            prompt=f"bash\n{broker_for_workspace.serialize_input(tool_input)}",
            context=context,
        )
        self.pending_approvals[approval.id] = PendingApproval(
            session_id=session_id,
            context=context,
        )
        await self.publish(
            TimelineEvent(
                session_id=session_id,
                type="approval.requested",
                content=f"Approval #{approval.id} requested for bash.",
            )
        )
        return f"Approval required before running `bash`. Review approval #{approval.id} above the composer."

    async def _resume_agent_loop_after_approval(self, session_id: str, context: dict[str, object]) -> str | None:
        workspace_raw = context.get("workspace")
        messages = context.get("messages")
        tool_use_id = context.get("tool_use_id")
        tool_name = context.get("tool_name")
        tool_input = context.get("tool_input")
        allow_subagent_tool = bool(context.get("allow_subagent_tool", True))
        agent_kind = str(context.get("agent_kind", "lead"))
        emit_stream_events = bool(context.get("emit_stream_events", True))

        if not isinstance(workspace_raw, str) or not isinstance(messages, list):
            return "Approval context is incomplete; unable to resume the pending action."
        if not isinstance(tool_use_id, str) or not isinstance(tool_name, str) or not isinstance(tool_input, dict):
            return "Approval context is incomplete; unable to execute the approved tool call."

        workspace = Path(workspace_raw)
        broker_for_workspace = ToolBroker(workspace)
        status, output = broker_for_workspace.run(tool_name, tool_input)
        tool_service.create_tool_execution(
            session_id=session_id,
            tool_name=tool_name,
            tool_source="local",
            server_name=None,
            status=status,
            input_json=broker_for_workspace.serialize_input(tool_input),
            output_text=output,
        )
        await self.publish(
            TimelineEvent(
                session_id=session_id,
                type="tool.execution",
                content=f"{tool_name} -> {status}",
            )
        )

        messages.append(
            {
                "role": "user",
                "content": [
                    {
                        "type": "tool_result",
                        "tool_use_id": tool_use_id,
                        "content": output,
                    }
                ],
            }
        )
        return await self._continue_agent_loop(
            session_id,
            workspace,
            messages,
            asyncio.Event(),
            allow_subagent_tool=allow_subagent_tool,
            agent_kind=agent_kind,
            emit_stream_events=emit_stream_events,
        )

    def _serialize_content_blocks(self, blocks: list[object]) -> list[dict[str, object]]:
        serialized: list[dict[str, object]] = []
        for block in blocks:
            block_type = getattr(block, "type", None)
            if block_type == "text" and isinstance(getattr(block, "text", None), str):
                serialized.append({"type": "text", "text": block.text})
                continue
            if block_type == "tool_use":
                serialized.append(
                    {
                        "type": "tool_use",
                        "id": str(getattr(block, "id", "")),
                        "name": str(getattr(block, "name", "")),
                        "input": dict(getattr(block, "input", {}) or {}),
                    }
                )
        return serialized

    def _chunk_text(self, text: str) -> list[str]:
        chunks: list[str] = []
        cursor = 0
        while cursor < len(text):
            next_cursor = min(cursor + 24, len(text))
            chunks.append(text[cursor:next_cursor])
            cursor = next_cursor
        return chunks or [text]
