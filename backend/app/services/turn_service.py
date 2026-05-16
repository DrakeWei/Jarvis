from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import select

from app.db.session import create_session
from app.models import SessionRecord, TurnRecord
from app.schemas.turns import TurnSummary


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _to_summary(row: TurnRecord) -> TurnSummary:
    return TurnSummary(
        id=row.id,
        session_id=row.session_id,
        user_message_id=row.user_message_id,
        workspace_path=row.workspace_path,
        workspace_fingerprint=row.workspace_fingerprint,
        status=row.status,
        started_at=row.started_at.isoformat(),
        updated_at=row.updated_at.isoformat(),
        completed_at=row.completed_at.isoformat() if row.completed_at else None,
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


def create_turn(session_id: str, user_message_id: int | None, workspace_path: str, workspace_fingerprint: str) -> TurnSummary:
    with create_session() as db:
        now = _utcnow()
        row = TurnRecord(
            session_id=session_id,
            user_message_id=user_message_id,
            workspace_path=workspace_path,
            workspace_fingerprint=workspace_fingerprint,
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


def list_turns(session_id: str | None = None) -> list[TurnSummary]:
    with create_session() as db:
        stmt = select(TurnRecord).order_by(TurnRecord.started_at.desc(), TurnRecord.id.desc())
        if session_id:
            stmt = stmt.where(TurnRecord.session_id == session_id)
        rows = db.scalars(stmt).all()
        return [_to_summary(row) for row in rows]


def latest_turn_by_status(session_id: str, statuses: tuple[str, ...]) -> TurnSummary | None:
    with create_session() as db:
        row = db.scalars(
            select(TurnRecord)
            .where(TurnRecord.session_id == session_id, TurnRecord.status.in_(statuses))
            .order_by(TurnRecord.started_at.desc(), TurnRecord.id.desc())
            .limit(1)
        ).first()
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
    with create_session() as db:
        rows = db.scalars(select(TurnRecord).where(TurnRecord.status == "running")).all()
        recovered: list[TurnSummary] = []
        now = _utcnow()
        for row in rows:
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
