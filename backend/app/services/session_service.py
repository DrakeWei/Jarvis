from datetime import timedelta

from sqlalchemy import delete, func, select

from app.core.config import settings
from app.core import workspace as workspace_utils
from app.db.session import create_session
from app.models import EventLogRecord, MessageAssetRecord, MessageRecord, SessionAssetRecord, SessionRecord
from app.schemas.events import MessageCreate, SessionCreate, SessionSummary, TimelineEvent
import app.services.asset_service as asset_service


def _to_session_summary(row: SessionRecord, updated_at: str) -> SessionSummary:
    return SessionSummary(
        session_id=row.id,
        title=row.title,
        workspace_mode=row.workspace_mode,
        canonical_workspace_path=row.canonical_workspace_path,
        workspace_label=row.workspace_label,
        workspace_fingerprint=row.workspace_fingerprint,
        status=row.status,
        created_at=row.created_at.isoformat(),
        updated_at=updated_at,
    )


def list_sessions() -> list[SessionSummary]:
    with create_session() as db:
        message_activity = (
            select(
                MessageRecord.session_id.label("session_id"),
                func.max(MessageRecord.created_at).label("updated_at"),
            )
            .group_by(MessageRecord.session_id)
            .subquery()
        )
        rows = db.execute(
            select(SessionRecord, message_activity.c.updated_at)
            .outerjoin(message_activity, message_activity.c.session_id == SessionRecord.id)
            .where(SessionRecord.hidden.is_(False))
            .order_by(
                func.coalesce(message_activity.c.updated_at, SessionRecord.created_at).desc(),
                SessionRecord.id.desc(),
            )
        ).all()
        return [
            _to_session_summary(
                row,
                (updated_at or row.created_at).isoformat(),
            )
            for row, updated_at in rows
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
        return _to_session_summary(row, row.created_at.isoformat())


def create_session_record(payload: SessionCreate) -> SessionSummary:
    workspace_mode = payload.workspace_mode
    workspace = workspace_utils.normalize_workspace_path(payload.workspace_path) if workspace_mode == "bound" else workspace_utils.normalize_workspace_path(settings.project_root)
    with create_session() as db:
        row = SessionRecord(
            title=payload.title,
            workspace_mode=workspace_mode,
            canonical_workspace_path=workspace.as_posix(),
            workspace_fingerprint=workspace_utils.workspace_fingerprint(workspace),
            workspace_label=workspace_utils.workspace_label(workspace) if workspace_mode == "bound" else "Default Conversations",
            status="idle",
        )
        db.add(row)
        db.commit()
        db.refresh(row)
        return _to_session_summary(row, row.created_at.isoformat())


def soft_delete_session(session_id: str) -> bool:
    with create_session() as db:
        row = db.get(SessionRecord, session_id)
        if row is None or row.hidden:
            return False
        row.hidden = True
        db.commit()
        return True


def create_message_record(session_id: str, payload: MessageCreate) -> MessageRecord:
    with create_session() as db:
        try:
            row = MessageRecord(session_id=session_id, role=payload.role, content=payload.content)
            db.add(row)
            db.flush()
            if payload.asset_ids:
                asset_service.link_message_assets(row.id, session_id, payload.asset_ids, db=db)
            db.commit()
            db.refresh(row)
            return row
        except Exception:
            db.rollback()
            raise


def list_message_records(session_id: str, limit: int | None = None) -> list[dict[str, object]]:
    with create_session() as db:
        rows = db.scalars(
            select(MessageRecord)
            .where(MessageRecord.session_id == session_id)
            .order_by(MessageRecord.created_at.asc(), MessageRecord.id.asc())
        ).all()
        if limit is not None and limit > 0:
            rows = rows[-limit:]
        message_ids = [row.id for row in rows]
        asset_refs_by_message_id: dict[int, list[dict[str, object]]] = {}
        if message_ids:
            linked_assets = db.execute(
                select(MessageAssetRecord.message_id, SessionAssetRecord)
                .join(SessionAssetRecord, SessionAssetRecord.id == MessageAssetRecord.asset_id)
                .where(
                    MessageAssetRecord.message_id.in_(message_ids),
                    SessionAssetRecord.session_id == session_id,
                    SessionAssetRecord.hidden.is_(False),
                )
                .order_by(MessageAssetRecord.id.asc())
            ).all()
            for message_id, asset_row in linked_assets:
                asset_refs_by_message_id.setdefault(int(message_id), []).append(
                    {
                        "type": "asset_ref",
                        "asset_id": asset_row.id,
                        "filename": asset_row.filename,
                        "kind": asset_row.kind,
                        "status": asset_row.status,
                    }
                )
        messages: list[dict[str, object]] = []
        for row in rows:
            asset_refs = asset_refs_by_message_id.get(row.id, [])
            if not asset_refs:
                messages.append(
                    {
                        "role": row.role,
                        "content": row.content,
                    }
                )
                continue

            content_parts: list[dict[str, object]] = []
            if row.content.strip():
                content_parts.append({"type": "text", "text": row.content})
            content_parts.extend(asset_refs)
            messages.append(
                {
                    "role": row.role,
                    "content": content_parts or row.content,
                }
            )
        return messages


def has_user_messages(session_id: str) -> bool:
    with create_session() as db:
        row = db.scalars(
            select(MessageRecord)
            .where(MessageRecord.session_id == session_id, MessageRecord.role == "user")
            .limit(1)
        ).first()
        return row is not None


def list_event_records(
    session_id: str,
    *,
    after_id: int | None = None,
    limit: int | None = None,
    include_ephemeral: bool = False,
) -> list[TimelineEvent]:
    with create_session() as db:
        stmt = (
            select(EventLogRecord)
            .where(EventLogRecord.session_id == session_id)
            .order_by(EventLogRecord.created_at.asc(), EventLogRecord.id.asc())
        )
        if not include_ephemeral:
            stmt = stmt.where(EventLogRecord.ephemeral.is_(False))
        if after_id is not None:
            stmt = stmt.where(EventLogRecord.id > after_id)
        if limit is not None and limit > 0:
            stmt = stmt.limit(limit)
        rows = db.scalars(stmt).all()
        return [
            TimelineEvent(
                event_id=row.id,
                session_id=row.session_id,
                type=row.event_type,
                content=row.content,
                created_at=row.created_at.isoformat(),
            )
            for row in rows
        ]


def create_event_record(event: TimelineEvent, *, ephemeral: bool = False) -> TimelineEvent:
    with create_session() as db:
        row = EventLogRecord(
            session_id=event.session_id,
            event_type=event.type,
            content=event.content,
            ephemeral=ephemeral,
        )
        db.add(row)
        db.commit()
        db.refresh(row)
        return TimelineEvent(
            event_id=row.id,
            session_id=row.session_id,
            type=row.event_type,
            content=row.content,
            created_at=row.created_at.isoformat(),
        )


def purge_expired_ephemeral_events() -> int:
    with create_session() as db:
        from datetime import datetime, timezone

        cutoff = datetime.now(timezone.utc) - timedelta(seconds=settings.jarvis_ephemeral_event_ttl_seconds)
        result = db.execute(
            delete(EventLogRecord).where(
                EventLogRecord.ephemeral.is_(True),
                EventLogRecord.created_at < cutoff,
            )
        )
        db.commit()
        return int(result.rowcount or 0)
