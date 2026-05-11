import asyncio
from collections import defaultdict

from app.schemas.events import MessageCreate, SessionCreate, SessionSummary, TimelineEvent
from app.services import session_service, tool_service
from app.tools import broker


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

    def list_sessions(self) -> list[SessionSummary]:
        return session_service.list_sessions()

    def session_exists(self, session_id: str) -> bool:
        return session_service.get_session(session_id) is not None

    def list_timeline(self, session_id: str) -> list[TimelineEvent]:
        return session_service.list_event_records(session_id)

    def list_tool_executions(self, session_id: str | None = None):
        return tool_service.list_tool_executions(session_id)

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
        tool_name, tool_payload = self._select_tool(content)
        assistant_parts: list[str] = []
        if tool_name:
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
            assistant_parts.append(self._summarize_tool_output(tool_name, output, record.status))
        if not assistant_parts:
            assistant_parts.append(
                "Lead runtime scaffold received your message. Tool routing is active for workspace listing, file reads, and explicit bash commands."
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

    def _select_tool(self, content: str) -> tuple[str | None, dict[str, object]]:
        text = content.strip()
        lowered = text.lower()
        if lowered.startswith("bash:"):
            return "bash", {"command": text.split(":", 1)[1].strip()}
        if lowered.startswith("read "):
            return "read_file", {"path": text.split(" ", 1)[1].strip()}
        if any(token in lowered for token in ["list files", "show files", "workspace", "project structure", "目录"]):
            return "list_files", {}
        return None, {}

    def _summarize_tool_output(self, tool_name: str, output: str, status: str) -> str:
        preview = output[:1000]
        if tool_name == "list_files":
            return f"Workspace snapshot completed with status `{status}`.\n\n{preview}"
        if tool_name == "read_file":
            return f"File read completed with status `{status}`.\n\n{preview}"
        if tool_name == "bash":
            return f"Bash command completed with status `{status}`.\n\n{preview}"
        return preview
