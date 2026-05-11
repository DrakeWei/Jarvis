import asyncio
from collections import defaultdict

from app.schemas.events import MessageCreate, SessionCreate, SessionSummary, TimelineEvent
from app.services import session_service


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
            await self.publish(
                TimelineEvent(
                    session_id=session_id,
                    type="runtime.notice",
                    content="Lead runtime scaffold is connected. Persistence and event replay are now live. Tool broker and teammate orchestration are next.",
                )
            )
