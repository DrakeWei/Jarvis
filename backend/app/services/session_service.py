from sqlalchemy import select

from app.db.session import create_session
from app.models import EventLogRecord, MessageRecord, SessionRecord
from app.schemas.events import MessageCreate, SessionCreate, SessionSummary, TimelineEvent


def list_sessions() -> list[SessionSummary]:
    with create_session() as db:
        rows = db.scalars(select(SessionRecord).order_by(SessionRecord.created_at.desc())).all()
        return [
            SessionSummary(
                session_id=row.id,
                title=row.title,
                created_at=row.created_at.isoformat(),
            )
            for row in rows
        ]


def get_session(session_id: str) -> SessionRecord | None:
    with create_session() as db:
        return db.get(SessionRecord, session_id)


def create_session_record(payload: SessionCreate) -> SessionSummary:
    with create_session() as db:
        row = SessionRecord(title=payload.title)
        db.add(row)
        db.commit()
        db.refresh(row)
        return SessionSummary(
            session_id=row.id,
            title=row.title,
            created_at=row.created_at.isoformat(),
        )


def create_message_record(session_id: str, payload: MessageCreate) -> None:
    with create_session() as db:
        db.add(MessageRecord(session_id=session_id, role=payload.role, content=payload.content))
        db.commit()


def list_event_records(session_id: str) -> list[TimelineEvent]:
    with create_session() as db:
        rows = db.scalars(
            select(EventLogRecord)
            .where(EventLogRecord.session_id == session_id)
            .order_by(EventLogRecord.created_at.asc(), EventLogRecord.id.asc())
        ).all()
        return [
            TimelineEvent(
                session_id=row.session_id,
                type=row.event_type,
                content=row.content,
                created_at=row.created_at.isoformat(),
            )
            for row in rows
        ]


def create_event_record(event: TimelineEvent) -> TimelineEvent:
    with create_session() as db:
        row = EventLogRecord(
            session_id=event.session_id,
            event_type=event.type,
            content=event.content,
        )
        db.add(row)
        db.commit()
        db.refresh(row)
        return TimelineEvent(
            session_id=row.session_id,
            type=row.event_type,
            content=row.content,
            created_at=row.created_at.isoformat(),
        )
