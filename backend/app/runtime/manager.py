import asyncio
import re
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path

from app.core.config import settings
from app.providers import TextBlock, ToolUseBlock, create_client
from app.schemas.events import MessageCreate, SessionCreate, SessionSummary, TimelineEvent
from app.schemas.subagents import SubagentRunCreate
from app.schemas.teammates import TeammateCreate
from app.services import approval_service, session_service, subagent_service, teammate_service, tool_service
from app.tools.broker import ToolBroker, broker


@dataclass
class PendingApproval:
    session_id: str
    context: dict[str, object]


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
        summary = self._generate_subagent_summary(payload.prompt)
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

    async def publish(self, event: TimelineEvent) -> TimelineEvent:
        stored = session_service.create_event_record(event)
        await self.events.publish(stored)
        return stored

    async def emit_ephemeral(self, event: TimelineEvent) -> TimelineEvent:
        await self.events.publish(event)
        return event

    def _start_background_turn(self, session_id: str, content: str) -> None:
        task = asyncio.create_task(self._run_lead_turn(session_id, content))
        self.background_tasks.add(task)
        task.add_done_callback(self.background_tasks.discard)

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
        if payload.role == "user":
            self._maybe_autoname_session(session_id, payload.content)
        session_service.create_message_record(session_id, payload)
        await self.publish(
            TimelineEvent(
                session_id=session_id,
                type=f"message.{payload.role}",
                content=payload.content,
            )
        )
        if payload.role == "user":
            self._start_background_turn(session_id, payload.content)

    async def _run_lead_turn(self, session_id: str, content: str) -> None:
        await self.emit_ephemeral(
            TimelineEvent(
                session_id=session_id,
                type="runtime.state",
                content="Lead runtime is evaluating the latest user turn.",
            )
        )
        try:
            reply = await self._run_agent_task(session_id, content)
            await self._publish_assistant_reply(session_id, reply)
            return
        except Exception as exc:
            reply = f"Lead runtime failed: {exc}"

        await self._publish_assistant_reply(session_id, reply)

    async def _run_agent_task(self, session_id: str, latest_user_content: str) -> str:
        workspace = self._resolve_request_workspace(latest_user_content)
        if workspace is None:
            return "我没有定位到你指定的目标项目路径。请给我更明确的本地项目名或绝对路径。"

        messages: list[dict[str, object]] = session_service.list_message_records(session_id, limit=12)
        return await self._continue_agent_loop(session_id, workspace, messages)

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

    def _autonomous_tool_schemas(self) -> list[dict[str, object]]:
        return [
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
        ]

    def _maybe_autoname_session(self, session_id: str, content: str) -> None:
        session = session_service.get_session(session_id)
        if session is None:
            return
        if not session.title.startswith("New Session") and session.title != "New Session":
            return
        if session_service.has_user_messages(session_id):
            return
        title = self._summarize_session_title(content)
        if title:
            session_service.update_session_title(session_id, title)

    def _summarize_session_title(self, content: str) -> str:
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
                "If the user explicitly writes command-like instructions such as `read ...`, `write ...`, `edit ...`, or `bash: ...`, treat them as direct tool intents.",
                "Do not ask the user to type explicit tool commands such as read or bash just because you need workspace facts.",
                "Use bash only when necessary; bash requires approval before execution.",
                "After using tools, answer the user's request directly.",
                "Do not mention that you used tools, inspected files, checked the workspace, or can continue using tools unless the user explicitly asks about your method or asks for next steps.",
                "For read-only questions, give the result directly instead of writing a work-log style summary.",
                "Do not append extra offers such as 'if you want I can continue...' unless the user asked for options or follow-up help.",
            ]
        )

    async def _stream_agent_response(
        self,
        *,
        client,
        session_id: str,
        workspace: Path,
        messages: list[dict[str, object]],
        tools: list[dict[str, object]],
    ) -> list[object] | None:
        loop = asyncio.get_running_loop()
        queue: asyncio.Queue[tuple[str, object | None]] = asyncio.Queue()

        def worker() -> None:
            try:
                for event in client.stream_response(
                    model=settings.model_id,
                    system=self._build_agent_system_prompt(workspace),
                    messages=messages,
                    tools=tools,
                    max_tokens=settings.llm_max_tokens,
                ):
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
                kind, payload = await queue.get()
                if kind == "text_delta" and isinstance(payload, dict):
                    delta = str(payload.get("delta") or "")
                    if delta:
                        text_parts.append(delta)
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
    ) -> str:
        client = create_client()
        tools = self._autonomous_tool_schemas()
        broker_for_workspace = ToolBroker(workspace)

        for _ in range(10):
            streamed_blocks = await self._stream_agent_response(
                client=client,
                session_id=session_id,
                workspace=workspace,
                messages=messages,
                tools=tools,
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
                if block.name == "bash":
                    return await self._queue_bash_approval(session_id, workspace, messages, block.id, block.input)

                status, output = broker_for_workspace.run(block.name, block.input)
                tool_service.create_tool_execution(
                    session_id=session_id,
                    tool_name=block.name,
                    status=status,
                    input_json=broker_for_workspace.serialize_input(block.input),
                    output_text=output,
                )
                await self.publish(
                    TimelineEvent(
                        session_id=session_id,
                        type="tool.execution",
                        content=f"{block.name} -> {status}",
                    )
                )
                results.append(
                    {
                        "type": "tool_result",
                        "tool_use_id": block.id,
                        "content": output,
                    }
                )
            messages.append({"role": "user", "content": results})

        return "任务执行达到了安全迭代上限，我先停在这里。你可以让我继续，或告诉我希望收敛到哪一步。"

    async def _queue_bash_approval(
        self,
        session_id: str,
        workspace: Path,
        messages: list[dict[str, object]],
        tool_use_id: str,
        tool_input: dict[str, object],
    ) -> str:
        broker_for_workspace = ToolBroker(workspace)
        context = {
            "mode": "agent_loop",
            "workspace": workspace.as_posix(),
            "messages": messages,
            "tool_use_id": tool_use_id,
            "tool_name": "bash",
            "tool_input": tool_input,
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
        return await self._continue_agent_loop(session_id, workspace, messages)

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
