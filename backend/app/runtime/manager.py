import asyncio
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timezone
from uuid import uuid4

from app.schemas.events import MessageCreate, SessionCreate, SessionSummary, TimelineEvent


@dataclass
class SessionState:
    session_id: str
    title: str
    created_at: str
    messages: list[MessageCreate] = field(default_factory=list)


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
        self.sessions: dict[str, SessionState] = {}
        self.events = EventBroker()

    def list_sessions(self) -> list[SessionSummary]:
        return [
            SessionSummary(
                session_id=session.session_id,
                title=session.title,
                created_at=session.created_at,
            )
            for session in self.sessions.values()
        ]

    async def create_session(self, payload: SessionCreate) -> SessionSummary:
        session_id = str(uuid4())
        created_at = datetime.now(timezone.utc).isoformat()
        session = SessionState(
            session_id=session_id,
            title=payload.title,
            created_at=created_at,
        )
        self.sessions[session_id] = session
        await self.events.publish(
            TimelineEvent(
                session_id=session_id,
                type="session.created",
                content=f"Session '{payload.title}' created.",
            )
        )
        return SessionSummary(
            session_id=session_id,
            title=payload.title,
            created_at=created_at,
        )

    async def append_message(self, session_id: str, payload: MessageCreate) -> None:
        session = self.sessions[session_id]
        session.messages.append(payload)
        await self.events.publish(
            TimelineEvent(
                session_id=session_id,
                type=f"message.{payload.role}",
                content=payload.content,
            )
        )
        if payload.role == "user":
            await self.events.publish(
                TimelineEvent(
                    session_id=session_id,
                    type="runtime.notice",
                    content="Lead runtime scaffold is connected. Tool broker, teammate manager, and persistence will plug into this stream.",
                )
            )
