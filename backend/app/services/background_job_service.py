from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import and_, delete, func, or_, select

from app.core.config import settings
from app.db.session import create_session
from app.models import BackgroundJobRecord
from app.schemas.background_jobs import BackgroundJobSummary


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def create_job(
    *,
    session_id: str | None,
    job_type: str,
    payload: dict[str, Any],
    command: str = "",
) -> BackgroundJobRecord:
    with create_session() as db:
        now = _utcnow()
        row = BackgroundJobRecord(
            session_id=session_id,
            job_type=job_type,
            command=command or job_type,
            status="queued",
            payload_json=json.dumps(payload, ensure_ascii=True),
            attempts=0,
            next_attempt_at=now,
            updated_at=now,
        )
        db.add(row)
        db.commit()
        db.refresh(row)
        return row


def _to_summary(row: BackgroundJobRecord) -> BackgroundJobSummary:
    return BackgroundJobSummary(
        id=row.id,
        session_id=row.session_id,
        job_type=row.job_type,
        command=row.command,
        status=row.status,
        owner_id=row.owner_id,
        attempts=row.attempts,
        next_attempt_at=row.next_attempt_at.isoformat() if row.next_attempt_at else None,
        output_text=row.output_text,
        created_at=row.created_at.isoformat(),
        updated_at=row.updated_at.isoformat(),
        started_at=row.started_at.isoformat() if row.started_at else None,
        completed_at=row.completed_at.isoformat() if row.completed_at else None,
    )


def get_job(job_id: int) -> BackgroundJobRecord | None:
    with create_session() as db:
        return db.get(BackgroundJobRecord, job_id)


def get_job_summary(job_id: int) -> BackgroundJobSummary | None:
    row = get_job(job_id)
    return _to_summary(row) if row else None


def list_job_summaries(
    *,
    session_id: str | None = None,
    job_type: str | None = None,
    status: str | None = None,
    limit: int = 100,
) -> list[BackgroundJobSummary]:
    with create_session() as db:
        stmt = select(BackgroundJobRecord).order_by(
            BackgroundJobRecord.created_at.desc(),
            BackgroundJobRecord.id.desc(),
        )
        if session_id:
            stmt = stmt.where(BackgroundJobRecord.session_id == session_id)
        if job_type:
            stmt = stmt.where(BackgroundJobRecord.job_type == job_type)
        if status:
            stmt = stmt.where(BackgroundJobRecord.status == status)
        rows = db.scalars(stmt.limit(max(1, min(limit, 200)))).all()
        return [_to_summary(row) for row in rows]


def list_recoverable_jobs(job_type: str | None = None) -> list[BackgroundJobRecord]:
    with create_session() as db:
        now = _utcnow()
        stmt = select(BackgroundJobRecord).where(
            or_(
                and_(
                    BackgroundJobRecord.status == "queued",
                    or_(
                        BackgroundJobRecord.next_attempt_at.is_(None),
                        BackgroundJobRecord.next_attempt_at <= now,
                    ),
                ),
                BackgroundJobRecord.status == "running",
            )
        )
        if job_type:
            stmt = stmt.where(BackgroundJobRecord.job_type == job_type)
        stmt = stmt.order_by(BackgroundJobRecord.created_at.asc(), BackgroundJobRecord.id.asc())
        return list(db.scalars(stmt).all())


def update_job_running(job_id: int, owner_id: str) -> BackgroundJobRecord | None:
    with create_session() as db:
        row = db.get(BackgroundJobRecord, job_id)
        if row is None:
            return None
        now = _utcnow()
        row.status = "running"
        row.owner_id = owner_id
        row.attempts = int(row.attempts or 0) + 1
        row.started_at = now
        row.updated_at = now
        row.next_attempt_at = now
        db.commit()
        db.refresh(row)
        return row


def update_job_completed(job_id: int, output_text: str = "") -> BackgroundJobRecord | None:
    with create_session() as db:
        row = db.get(BackgroundJobRecord, job_id)
        if row is None:
            return None
        now = _utcnow()
        row.status = "completed"
        row.output_text = output_text or row.output_text
        row.owner_id = None
        row.updated_at = now
        row.completed_at = now
        row.next_attempt_at = None
        db.commit()
        db.refresh(row)
        return row


def update_job_failed(job_id: int, error_text: str) -> BackgroundJobRecord | None:
    with create_session() as db:
        row = db.get(BackgroundJobRecord, job_id)
        if row is None:
            return None
        now = _utcnow()
        row.status = "failed"
        row.output_text = error_text
        row.owner_id = None
        row.updated_at = now
        row.completed_at = now
        row.next_attempt_at = None
        db.commit()
        db.refresh(row)
        return row


def update_job_dead_lettered(job_id: int, error_text: str) -> BackgroundJobRecord | None:
    with create_session() as db:
        row = db.get(BackgroundJobRecord, job_id)
        if row is None:
            return None
        now = _utcnow()
        row.status = "dead_lettered"
        row.output_text = error_text
        row.owner_id = None
        row.updated_at = now
        row.completed_at = now
        row.next_attempt_at = None
        db.commit()
        db.refresh(row)
        return row


def requeue_job(job_id: int, error_text: str, *, delay_seconds: int | None = None) -> BackgroundJobRecord | None:
    with create_session() as db:
        row = db.get(BackgroundJobRecord, job_id)
        if row is None:
            return None
        now = _utcnow()
        delay = max(1, delay_seconds if delay_seconds is not None else settings.jarvis_background_job_base_backoff_seconds)
        row.status = "queued"
        row.output_text = error_text
        row.owner_id = None
        row.updated_at = now
        row.next_attempt_at = now + timedelta(seconds=delay)
        db.commit()
        db.refresh(row)
        return row


def retry_job_now(job_id: int) -> BackgroundJobRecord | None:
    with create_session() as db:
        row = db.get(BackgroundJobRecord, job_id)
        if row is None:
            return None
        if row.status not in {"failed", "dead_lettered"}:
            raise ValueError(f"Background job #{job_id} is not retryable from status '{row.status}'.")
        now = _utcnow()
        row.status = "queued"
        row.owner_id = None
        row.attempts = 0
        row.started_at = None
        row.completed_at = None
        row.next_attempt_at = now
        row.updated_at = now
        db.commit()
        db.refresh(row)
        return row


def cancel_job(job_id: int, reason: str = "cancelled by operator") -> BackgroundJobRecord | None:
    with create_session() as db:
        row = db.get(BackgroundJobRecord, job_id)
        if row is None:
            return None
        if row.status in {"completed", "cancelled"}:
            return row
        now = _utcnow()
        row.status = "cancelled"
        row.output_text = reason
        row.owner_id = None
        row.updated_at = now
        row.completed_at = now
        row.next_attempt_at = None
        db.commit()
        db.refresh(row)
        return row


def should_retry(row: BackgroundJobRecord | None) -> bool:
    if row is None:
        return False
    return int(row.attempts or 0) < max(1, settings.jarvis_background_job_max_attempts)


def status_counts(job_type: str | None = None) -> dict[str, int]:
    with create_session() as db:
        stmt = select(BackgroundJobRecord.status, func.count()).group_by(BackgroundJobRecord.status)
        if job_type:
            stmt = stmt.where(BackgroundJobRecord.job_type == job_type)
        rows = db.execute(stmt).all()
        return {str(status): int(count) for status, count in rows}


def oldest_queued_age_seconds(job_type: str | None = None) -> float | None:
    with create_session() as db:
        stmt = select(func.min(BackgroundJobRecord.created_at)).where(BackgroundJobRecord.status == "queued")
        if job_type:
            stmt = stmt.where(BackgroundJobRecord.job_type == job_type)
        oldest = db.execute(stmt).scalar_one_or_none()
        if oldest is None:
            return None
        if oldest.tzinfo is None:
            oldest = oldest.replace(tzinfo=timezone.utc)
        return max(0.0, (_utcnow() - oldest).total_seconds())


def oldest_running_age_seconds(job_type: str | None = None) -> float | None:
    with create_session() as db:
        stmt = select(func.min(BackgroundJobRecord.started_at)).where(
            BackgroundJobRecord.status == "running",
            BackgroundJobRecord.started_at.is_not(None),
        )
        if job_type:
            stmt = stmt.where(BackgroundJobRecord.job_type == job_type)
        oldest = db.execute(stmt).scalar_one_or_none()
        if oldest is None:
            return None
        if oldest.tzinfo is None:
            oldest = oldest.replace(tzinfo=timezone.utc)
        return max(0.0, (_utcnow() - oldest).total_seconds())


def retrying_count(job_type: str | None = None) -> int:
    with create_session() as db:
        stmt = select(func.count()).select_from(BackgroundJobRecord).where(
            BackgroundJobRecord.status.in_(("queued", "running")),
            BackgroundJobRecord.attempts > 1,
        )
        if job_type:
            stmt = stmt.where(BackgroundJobRecord.job_type == job_type)
        count = db.execute(stmt).scalar_one()
        return int(count or 0)


def payload_dict(row: BackgroundJobRecord) -> dict[str, Any]:
    raw = row.payload_json or "{}"
    try:
        decoded = json.loads(raw)
    except Exception:
        return {}
    return decoded if isinstance(decoded, dict) else {}


def purge_terminal_jobs() -> int:
    with create_session() as db:
        cutoff = _utcnow() - timedelta(seconds=settings.jarvis_completed_background_job_ttl_seconds)
        result = db.execute(
            delete(BackgroundJobRecord).where(
                BackgroundJobRecord.status.in_(("completed", "failed", "dead_lettered")),
                BackgroundJobRecord.completed_at.is_not(None),
                BackgroundJobRecord.completed_at < cutoff,
            )
        )
        db.commit()
        return int(result.rowcount or 0)
