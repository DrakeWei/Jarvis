from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import func, select

from app.db.session import create_session
from app.models import ExecutionLeaseRecord, SessionRecord, TurnRecord
from app.schemas.turns import TurnSummary


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _as_utc(value: datetime) -> datetime:
    return value if value.tzinfo is not None else value.replace(tzinfo=timezone.utc)


def _to_summary(row: TurnRecord) -> TurnSummary:
    return TurnSummary(
        id=row.id,
        session_id=row.session_id,
        branch_context_id=row.branch_context_id,
        user_message_id=row.user_message_id,
        workspace_path=row.workspace_path,
        workspace_fingerprint=row.workspace_fingerprint,
        execution_mode=row.execution_mode,
        status=row.status,
        started_at=row.started_at.isoformat(),
        updated_at=row.updated_at.isoformat(),
        completed_at=row.completed_at.isoformat() if row.completed_at else None,
        cancel_requested=bool(row.cancel_requested),
        last_checkpoint_seq=row.last_checkpoint_seq,
        resume_hint=row.resume_hint,
        error_summary=row.error_summary,
    )


def _sync_session_status(db, session_id: str, turn_status: str) -> None:
    session = db.get(SessionRecord, session_id)
    if session is None:
        return
    session.status = {
        "queued": "running",
        "running": "running",
        "waiting_approval": "waiting_approval",
        "interrupted": "interrupted",
        "failed": "failed",
    }.get(turn_status, "idle")


def create_turn(
    session_id: str,
    user_message_id: int | None,
    workspace_path: str,
    workspace_fingerprint: str,
    *,
    branch_context_id: str | None = None,
    execution_mode: str = "normal",
) -> TurnSummary:
    with create_session() as db:
        now = _utcnow()
        row = TurnRecord(
            session_id=session_id,
            branch_context_id=branch_context_id,
            user_message_id=user_message_id,
            workspace_path=workspace_path,
            workspace_fingerprint=workspace_fingerprint,
            execution_mode=execution_mode,
            status="queued",
            started_at=now,
            updated_at=now,
            last_checkpoint_seq=0,
        )
        db.add(row)
        _sync_session_status(db, session_id, row.status)
        db.commit()
        db.refresh(row)
        return _to_summary(row)


def get_turn(turn_id: int) -> TurnSummary | None:
    with create_session() as db:
        row = db.get(TurnRecord, turn_id)
        return _to_summary(row) if row else None


def list_turns(session_id: str | None = None, *, branch_context_id: str | None = None) -> list[TurnSummary]:
    with create_session() as db:
        stmt = select(TurnRecord).order_by(TurnRecord.started_at.desc(), TurnRecord.id.desc())
        if session_id:
            stmt = stmt.where(TurnRecord.session_id == session_id)
        if branch_context_id is not None:
            stmt = stmt.where(TurnRecord.branch_context_id == branch_context_id)
        rows = db.scalars(stmt).all()
        return [_to_summary(row) for row in rows]


def latest_turn_by_status(session_id: str, statuses: tuple[str, ...], *, branch_context_id: str | None = None) -> TurnSummary | None:
    with create_session() as db:
        stmt = (
            select(TurnRecord)
            .where(TurnRecord.session_id == session_id, TurnRecord.status.in_(statuses))
            .order_by(TurnRecord.started_at.desc(), TurnRecord.id.desc())
            .limit(1)
        )
        if branch_context_id is not None:
            stmt = stmt.where(TurnRecord.branch_context_id == branch_context_id)
        row = db.scalars(stmt).first()
        return _to_summary(row) if row else None


def update_turn_status(
    turn_id: int,
    status: str,
    *,
    resume_hint: str | None = None,
    error_summary: str | None = None,
    completed: bool | None = None,
) -> TurnSummary | None:
    with create_session() as db:
        row = db.get(TurnRecord, turn_id)
        if row is None:
            return None
        row.status = status
        row.updated_at = _utcnow()
        if status in {"completed", "cancelled", "failed", "interrupted"}:
            row.cancel_requested = False
        if resume_hint is not None:
            row.resume_hint = resume_hint
        if error_summary is not None:
            row.error_summary = error_summary
        terminal = completed if completed is not None else status in {"completed", "cancelled", "failed"}
        if terminal:
            row.completed_at = _utcnow()
        _sync_session_status(db, row.session_id, row.status)
        db.commit()
        db.refresh(row)
        return _to_summary(row)


def recover_running_turns() -> list[TurnSummary]:
    return recover_orphaned_running_turns()


def recover_orphaned_running_turns() -> list[TurnSummary]:
    with create_session() as db:
        rows = db.scalars(select(TurnRecord).where(TurnRecord.status == "running")).all()
        lease_rows = db.scalars(
            select(ExecutionLeaseRecord).where(
                ExecutionLeaseRecord.scope_type == "turn",
                ExecutionLeaseRecord.status == "active",
            )
        ).all()
        active_leases = {
            row.scope_key
            for row in lease_rows
            if _as_utc(row.expires_at) > _utcnow()
        }
        recovered: list[TurnSummary] = []
        now = _utcnow()
        for row in rows:
            if str(row.id) in active_leases:
                continue
            row.status = "interrupted"
            row.updated_at = now
            if not row.resume_hint:
                row.resume_hint = "Runtime restarted while this turn was still running."
            _sync_session_status(db, row.session_id, row.status)
            recovered.append(_to_summary(row))
        db.commit()
        return recovered


def refresh_waiting_approval_sessions() -> list[TurnSummary]:
    with create_session() as db:
        rows = db.scalars(select(TurnRecord).where(TurnRecord.status == "waiting_approval")).all()
        summaries: list[TurnSummary] = []
        for row in rows:
            _sync_session_status(db, row.session_id, row.status)
            summaries.append(_to_summary(row))
        db.commit()
        return summaries


def request_turn_cancel(turn_id: int) -> TurnSummary | None:
    with create_session() as db:
        row = db.get(TurnRecord, turn_id)
        if row is None:
            return None
        row.cancel_requested = True
        row.updated_at = _utcnow()
        db.commit()
        db.refresh(row)
        return _to_summary(row)


def latest_cancellable_turn(session_id: str, *, branch_context_id: str | None = None) -> TurnSummary | None:
    return latest_turn_by_status(session_id, ("queued", "running", "waiting_approval"), branch_context_id=branch_context_id)


def is_cancel_requested(turn_id: int) -> bool:
    with create_session() as db:
        row = db.get(TurnRecord, turn_id)
        return bool(row and row.cancel_requested)


def has_newer_turn(session_id: str, turn_id: int, *, branch_context_id: str | None = None) -> bool:
    with create_session() as db:
        stmt = (
            select(TurnRecord.id)
            .where(TurnRecord.session_id == session_id, TurnRecord.id > turn_id)
            .order_by(TurnRecord.id.desc())
            .limit(1)
        )
        if branch_context_id is not None:
            stmt = stmt.where(TurnRecord.branch_context_id == branch_context_id)
        row = db.scalars(stmt).first()
        return row is not None


def oldest_running_turn_age_seconds() -> float | None:
    with create_session() as db:
        oldest = db.execute(
            select(func.min(TurnRecord.started_at)).where(TurnRecord.status == "running")
        ).scalar_one_or_none()
        if oldest is None:
            return None
        if oldest.tzinfo is None:
            oldest = oldest.replace(tzinfo=timezone.utc)
        return max(0.0, (_utcnow() - oldest).total_seconds())
