from sqlalchemy import func, select

from app.db.session import create_session
from app.models import EventLogRecord, MessageRecord, SessionRecord
from app.schemas.events import MessageCreate, SessionCreate, SessionSummary, TimelineEvent


def list_sessions() -> list[SessionSummary]:
    with create_session() as db:
        message_activity = {
            session_id: updated_at
            for session_id, updated_at in db.execute(
                select(MessageRecord.session_id, func.max(MessageRecord.created_at)).group_by(MessageRecord.session_id)
            ).all()
        }
        rows = db.scalars(select(SessionRecord).where(SessionRecord.hidden.is_(False))).all()
        rows = sorted(
            rows,
            key=lambda row: message_activity.get(row.id) or row.created_at,
            reverse=True,
        )
        return [
            SessionSummary(
                session_id=row.id,
                title=row.title,
                created_at=row.created_at.isoformat(),
                updated_at=(message_activity.get(row.id) or row.created_at).isoformat(),
            )
            for row in rows
        ]


def get_session(session_id: str) -> SessionRecord | None:
    with create_session() as db:
        row = db.get(SessionRecord, session_id)
        if row is None or row.hidden:
            return None
        return row


def update_session_title(session_id: str, title: str) -> SessionSummary | None:
    with create_session() as db:
        row = db.get(SessionRecord, session_id)
        if row is None or row.hidden:
            return None
        row.title = title
        db.commit()
        db.refresh(row)
        return SessionSummary(
            session_id=row.id,
            title=row.title,
            created_at=row.created_at.isoformat(),
            updated_at=row.created_at.isoformat(),
        )


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
            updated_at=row.created_at.isoformat(),
        )


def soft_delete_session(session_id: str) -> bool:
    with create_session() as db:
        row = db.get(SessionRecord, session_id)
        if row is None or row.hidden:
            return False
        row.hidden = True
        db.commit()
        return True


def create_message_record(session_id: str, payload: MessageCreate) -> None:
    with create_session() as db:
        db.add(MessageRecord(session_id=session_id, role=payload.role, content=payload.content))
        db.commit()


def list_message_records(session_id: str, limit: int | None = None) -> list[dict[str, str]]:
    with create_session() as db:
        rows = db.scalars(
            select(MessageRecord)
            .where(MessageRecord.session_id == session_id)
            .order_by(MessageRecord.created_at.asc(), MessageRecord.id.asc())
        ).all()
        if limit is not None and limit > 0:
            rows = rows[-limit:]
        return [
            {
                "role": row.role,
                "content": row.content,
            }
            for row in rows
        ]


def has_user_messages(session_id: str) -> bool:
    with create_session() as db:
        row = db.scalars(
            select(MessageRecord)
            .where(MessageRecord.session_id == session_id, MessageRecord.role == "user")
            .limit(1)
        ).first()
        return row is not None


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
