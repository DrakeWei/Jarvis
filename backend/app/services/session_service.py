import json
from datetime import timedelta
from uuid import uuid4

from sqlalchemy import delete, func, select

from app.core.config import settings
from app.core import workspace as workspace_utils
from app.db.session import create_session
from app.models import EventLogRecord, MessageAssetRecord, MessageRecord, SessionAssetRecord, SessionRecord
from app.schemas.events import MessageCreate, SessionCreate, SessionSummary, TimelineEvent
import app.services.asset_service as asset_service
import app.services.git_service as git_service


def _to_session_summary(row: SessionRecord, updated_at: str) -> SessionSummary:
    return SessionSummary(
        session_id=row.id,
        title=row.title,
        workspace_mode=row.workspace_mode,
        canonical_workspace_path=row.canonical_workspace_path,
        workspace_label=row.workspace_label,
        workspace_fingerprint=row.workspace_fingerprint,
        repo_root=row.repo_root,
        git_enabled=bool(row.git_enabled),
        lead_branch=row.lead_branch,
        head_revision=row.head_revision,
        working_tree_status=row.working_tree_status,
        detached_head=bool(row.detached_head),
        status=row.status,
        created_at=row.created_at.isoformat(),
        updated_at=updated_at,
    )


def _ensure_branch_context_id(row: SessionRecord) -> bool:
    if row.branch_context_id:
        return False
    row.branch_context_id = str(uuid4())
    return True


def _apply_git_state(row: SessionRecord) -> bool:
    state = git_service.inspect_workspace_git_state(row.canonical_workspace_path)
    changed = False
    mapping = {
        "repo_root": state.repo_root,
        "git_enabled": bool(state.git_enabled),
        "lead_branch": state.lead_branch,
        "head_revision": state.head_revision,
        "working_tree_status": state.working_tree_status,
        "detached_head": bool(state.detached_head),
    }
    for field, value in mapping.items():
        if getattr(row, field) != value:
            setattr(row, field, value)
            changed = True
    return changed


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
        changed = False
        for row, _updated_at in rows:
            if _ensure_branch_context_id(row):
                changed = True
            if _apply_git_state(row):
                changed = True
        if changed:
            db.commit()
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
        if _ensure_branch_context_id(row) or _apply_git_state(row):
            db.commit()
            db.refresh(row)
        return row


def get_branch_context_id(session_id: str) -> str | None:
    session = get_session(session_id)
    return session.branch_context_id if session else None


def rotate_branch_context(
    session_id: str,
    *,
    repo_root: str | None,
    lead_branch: str | None,
    head_revision: str | None,
    working_tree_status: str | None,
    detached_head: bool,
) -> SessionSummary | None:
    with create_session() as db:
        row = db.get(SessionRecord, session_id)
        if row is None or row.hidden:
            return None
        row.repo_root = repo_root
        row.git_enabled = bool(repo_root)
        row.lead_branch = lead_branch
        row.head_revision = head_revision
        row.working_tree_status = working_tree_status
        row.detached_head = detached_head
        row.branch_context_id = str(uuid4())
        db.commit()
        db.refresh(row)
        return _to_session_summary(row, row.created_at.isoformat())


def update_session_title(session_id: str, title: str) -> SessionSummary | None:
    with create_session() as db:
        row = db.get(SessionRecord, session_id)
        if row is None or row.hidden:
            return None
        _ensure_branch_context_id(row)
        row.title = title
        db.commit()
        db.refresh(row)
        return _to_session_summary(row, row.created_at.isoformat())


def create_session_record(payload: SessionCreate) -> SessionSummary:
    workspace_mode = payload.workspace_mode
    workspace = workspace_utils.normalize_workspace_path(payload.workspace_path) if workspace_mode == "bound" else workspace_utils.normalize_workspace_path(settings.project_root)
    git_state = git_service.inspect_workspace_git_state(workspace)
    with create_session() as db:
        row = SessionRecord(
            title=payload.title,
            workspace_mode=workspace_mode,
            canonical_workspace_path=workspace.as_posix(),
            workspace_fingerprint=workspace_utils.workspace_fingerprint(workspace),
            workspace_label=workspace_utils.workspace_label(workspace) if workspace_mode == "bound" else "Default Conversations",
            repo_root=git_state.repo_root,
            git_enabled=git_state.git_enabled,
            lead_branch=git_state.lead_branch,
            head_revision=git_state.head_revision,
            working_tree_status=git_state.working_tree_status,
            detached_head=git_state.detached_head,
            branch_context_id=str(uuid4()),
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
            session_row = db.get(SessionRecord, session_id)
            if session_row is not None:
                _ensure_branch_context_id(session_row)
            branch_context_id = session_row.branch_context_id if session_row else None
            row = MessageRecord(
                session_id=session_id,
                branch_context_id=branch_context_id,
                role=payload.role,
                content=payload.content,
            )
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


def list_message_records(session_id: str, limit: int | None = None, *, branch_context_id: str | None = None) -> list[dict[str, object]]:
    with create_session() as db:
        if branch_context_id is None:
            session_row = db.get(SessionRecord, session_id)
            branch_context_id = session_row.branch_context_id if session_row else None
        stmt = (
            select(MessageRecord)
            .where(MessageRecord.session_id == session_id)
            .order_by(MessageRecord.created_at.asc(), MessageRecord.id.asc())
        )
        if branch_context_id is not None:
            stmt = stmt.where(MessageRecord.branch_context_id == branch_context_id)
        rows = db.scalars(stmt).all()
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
                        "preview_path": asset_row.preview_path,
                        "storage_path": asset_row.storage_path,
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
                parts=_decode_event_parts(row.payload_json),
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
            payload_json=json.dumps(event.parts) if event.parts else None,
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
            parts=_decode_event_parts(row.payload_json),
            created_at=row.created_at.isoformat(),
        )


def _decode_event_parts(payload_json: str | None) -> list[dict[str, object]] | None:
    if not payload_json:
        return None
    try:
        loaded = json.loads(payload_json)
    except Exception:
        return None
    if not isinstance(loaded, list):
        return None
    return [item for item in loaded if isinstance(item, dict)]


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
