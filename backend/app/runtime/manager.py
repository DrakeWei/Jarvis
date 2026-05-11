import asyncio
from collections import defaultdict
from dataclasses import dataclass

from app.schemas.events import MessageCreate, SessionCreate, SessionSummary, TimelineEvent
from app.services import approval_service, session_service, tool_service
from app.tools import broker


@dataclass
class PendingApproval:
    session_id: str
    steps: list[tuple[str, dict[str, object]]]


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
            first_tool, first_payload = pending.steps[0]
            status, output = broker.run(first_tool, first_payload)
            record = tool_service.create_tool_execution(
                session_id=pending.session_id,
                tool_name=first_tool,
                status=status,
                input_json=broker.serialize_input(first_payload),
                output_text=output,
            )
            await self.publish(
                TimelineEvent(
                    session_id=pending.session_id,
                    type="tool.execution",
                    content=f"{record.tool_name} -> {record.status}",
                )
            )
            parts = [self._summarize_tool_output(first_tool, output, record.status)]
            more_parts, pause_message = await self._execute_steps(
                pending.session_id,
                pending.steps[1:],
            )
            parts.extend(more_parts)
            if pause_message:
                parts.append(pause_message)
            if parts:
                reply = "\n\n".join(parts)
                session_service.create_message_record(
                    pending.session_id,
                    MessageCreate(role="assistant", content=reply),
                )
                await self.publish(
                    TimelineEvent(
                        session_id=pending.session_id,
                        type="message.assistant",
                        content=reply,
                    )
                )
        return decision

    async def publish(self, event: TimelineEvent) -> TimelineEvent:
        stored = session_service.create_event_record(event)
        await self.events.publish(stored)
        return stored

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
        session_service.create_message_record(session_id, payload)
        await self.publish(
            TimelineEvent(
                session_id=session_id,
                type=f"message.{payload.role}",
                content=payload.content,
            )
        )
        if payload.role == "user":
            await self._run_lead_turn(session_id, payload.content)

    async def _run_lead_turn(self, session_id: str, content: str) -> None:
        await self.publish(
            TimelineEvent(
                session_id=session_id,
                type="runtime.state",
                content="Lead runtime is evaluating the latest user turn.",
            )
        )
        steps, guidance = self._plan_steps(content)
        assistant_parts: list[str] = []
        executed_parts, pause_message = await self._execute_steps(session_id, steps)
        assistant_parts.extend(executed_parts)
        if pause_message:
            assistant_parts.append(pause_message)
        if guidance:
            assistant_parts.append(guidance)
        if not assistant_parts:
            assistant_parts.append(
                "Lead runtime scaffold received your message. Tool routing is active for workspace listing, file reads, file writes, exact file edits, and explicit bash commands."
            )
        reply = "\n\n".join(assistant_parts)
        session_service.create_message_record(session_id, MessageCreate(role="assistant", content=reply))
        await self.publish(
            TimelineEvent(
                session_id=session_id,
                type="message.assistant",
                content=reply,
            )
        )

    async def _execute_steps(
        self,
        session_id: str,
        steps: list[tuple[str, dict[str, object]]],
    ) -> tuple[list[str], str | None]:
        parts: list[str] = []
        for index, (tool_name, tool_payload) in enumerate(steps):
            if self._requires_approval(tool_name):
                approval = approval_service.create_approval(
                    session_id=session_id,
                    approval_type=tool_name,
                    prompt=f"{tool_name}\n{broker.serialize_input(tool_payload)}",
                )
                self.pending_approvals[approval.id] = PendingApproval(
                    session_id=session_id,
                    steps=steps[index:],
                )
                await self.publish(
                    TimelineEvent(
                        session_id=session_id,
                        type="approval.requested",
                        content=f"Approval #{approval.id} requested for {tool_name}.",
                    )
                )
                return parts, f"Approval required before running `{tool_name}`. Review approval #{approval.id} in the cockpit."
            status, output = broker.run(tool_name, tool_payload)
            record = tool_service.create_tool_execution(
                session_id=session_id,
                tool_name=tool_name,
                status=status,
                input_json=broker.serialize_input(tool_payload),
                output_text=output,
            )
            await self.publish(
                TimelineEvent(
                    session_id=session_id,
                    type="tool.execution",
                    content=f"{record.tool_name} -> {record.status}",
                )
            )
            parts.append(self._summarize_tool_output(tool_name, output, record.status))
        return parts, None

    def _plan_steps(self, content: str) -> tuple[list[tuple[str, dict[str, object]]], str | None]:
        blocks = [part.strip() for part in content.split("\n\n") if part.strip()]
        if len(blocks) <= 1:
            tool_name, tool_payload, guidance = self._select_tool(content)
            return ([(tool_name, tool_payload)] if tool_name else []), guidance
        steps: list[tuple[str, dict[str, object]]] = []
        for block in blocks:
            tool_name, tool_payload, guidance = self._select_tool(block)
            if guidance:
                return [], guidance
            if tool_name:
                steps.append((tool_name, tool_payload))
        return steps, None

    def _requires_approval(self, tool_name: str) -> bool:
        return tool_name in {"bash", "write_file", "edit_file"}

    def _select_tool(self, content: str) -> tuple[str | None, dict[str, object], str | None]:
        text = content.strip()
        lowered = text.lower()
        if lowered.startswith("bash:"):
            return "bash", {"command": text.split(":", 1)[1].strip()}, None
        if lowered.startswith("read "):
            path = text.split(" ", 1)[1].strip()
            if not path:
                return None, {}, "Use `read <path>` with a file path, for example `read README.md`."
            return "read_file", {"path": path}, None
        if lowered.startswith("write "):
            parsed = self._parse_write_command(text)
            return ("write_file", parsed, None) if parsed else (None, {}, self._write_guidance())
        if lowered.startswith("edit "):
            parsed = self._parse_edit_command(text)
            return ("edit_file", parsed, None) if parsed else (None, {}, self._edit_guidance())
        if any(token in lowered for token in ["list files", "show files", "workspace", "project structure", "目录"]):
            return "list_files", {}, None
        return None, {}, None

    def _summarize_tool_output(self, tool_name: str, output: str, status: str) -> str:
        preview = output[:1000]
        if status in {"error", "blocked"}:
            return f"{tool_name} returned status `{status}`.\n\n{preview}"
        if tool_name == "list_files":
            return f"Workspace snapshot completed with status `{status}`.\n\n{preview}"
        if tool_name == "read_file":
            return f"File read completed with status `{status}`.\n\n{preview}"
        if tool_name == "write_file":
            return f"File write completed with status `{status}`.\n\n{preview}"
        if tool_name == "edit_file":
            return f"File edit completed with status `{status}`.\n\n{preview}"
        if tool_name == "bash":
            return f"Bash command completed with status `{status}`.\n\n{preview}"
        return preview

    def _parse_write_command(self, text: str) -> dict[str, object] | None:
        header, marker, body = text.partition("\n<<<\n")
        if not marker or not body.endswith("\n>>>"):
            return None
        path = header.split(" ", 1)[1].strip()
        content = body[:-4]
        return {"path": path, "content": content}

    def _parse_edit_command(self, text: str) -> dict[str, object] | None:
        header, marker, body = text.partition("\n<<<OLD\n")
        if not marker or "\n===\n" not in body or not body.endswith("\n>>>"):
            return None
        old_text, new_part = body[:-4].split("\n===\n", 1)
        path = header.split(" ", 1)[1].strip()
        return {"path": path, "old_text": old_text, "new_text": new_part}

    def _write_guidance(self) -> str:
        return "Write commands must use this format:\n\nwrite path/to/file.txt\n<<<\nnew file contents\n>>>"

    def _edit_guidance(self) -> str:
        return "Edit commands must use this format:\n\nedit path/to/file.txt\n<<<OLD\ntext to replace\n===\nreplacement text\n>>>"
