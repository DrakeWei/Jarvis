from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import delete, func, select

from app.core.config import settings
from app.db.session import create_session
from app.models import IngestionJobRecord
from app.schemas.assets import IngestionJobSummary


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _to_summary(row: IngestionJobRecord) -> IngestionJobSummary:
    return IngestionJobSummary(
        id=row.id,
        session_id=row.session_id,
        asset_id=row.asset_id,
        job_type=row.job_type,
        status=row.status,
        attempts=row.attempts,
        owner_id=row.owner_id,
        last_error=row.last_error,
        created_at=row.created_at.isoformat(),
        updated_at=row.updated_at.isoformat(),
        started_at=row.started_at.isoformat() if row.started_at else None,
        completed_at=row.completed_at.isoformat() if row.completed_at else None,
    )


def create_job(session_id: str, asset_id: str, *, job_type: str = "asset_ingestion") -> IngestionJobSummary:
    with create_session() as db:
        row = IngestionJobRecord(
            session_id=session_id,
            asset_id=asset_id,
            job_type=job_type,
            status="queued",
            attempts=0,
        )
        db.add(row)
        db.commit()
        db.refresh(row)
        return _to_summary(row)


def get_job(job_id: int) -> IngestionJobSummary | None:
    with create_session() as db:
        row = db.get(IngestionJobRecord, job_id)
        return _to_summary(row) if row else None


def update_job_running(job_id: int, owner_id: str) -> IngestionJobSummary | None:
    with create_session() as db:
        row = db.get(IngestionJobRecord, job_id)
        if row is None:
            return None
        now = _utcnow()
        row.status = "running"
        row.owner_id = owner_id
        row.last_error = None
        row.attempts = int(row.attempts or 0) + 1
        row.started_at = now
        row.updated_at = now
        db.commit()
        db.refresh(row)
        return _to_summary(row)


def update_job_completed(job_id: int) -> IngestionJobSummary | None:
    with create_session() as db:
        row = db.get(IngestionJobRecord, job_id)
        if row is None:
            return None
        now = _utcnow()
        row.status = "completed"
        row.updated_at = now
        row.completed_at = now
        db.commit()
        db.refresh(row)
        return _to_summary(row)


def update_job_failed(job_id: int, error: str) -> IngestionJobSummary | None:
    with create_session() as db:
        row = db.get(IngestionJobRecord, job_id)
        if row is None:
            return None
        now = _utcnow()
        row.status = "failed"
        row.last_error = error
        row.updated_at = now
        db.commit()
        db.refresh(row)
        return _to_summary(row)


def list_recoverable_jobs() -> list[IngestionJobSummary]:
    with create_session() as db:
        rows = db.scalars(
            select(IngestionJobRecord)
            .where(IngestionJobRecord.status.in_(("queued", "running")))
            .order_by(IngestionJobRecord.created_at.asc(), IngestionJobRecord.id.asc())
        ).all()
        return [_to_summary(row) for row in rows]


def status_counts() -> dict[str, int]:
    with create_session() as db:
        rows = db.execute(
            select(IngestionJobRecord.status, func.count()).group_by(IngestionJobRecord.status)
        ).all()
        return {str(status): int(count) for status, count in rows}


def oldest_queued_age_seconds() -> float | None:
    with create_session() as db:
        oldest = db.execute(
            select(func.min(IngestionJobRecord.created_at)).where(IngestionJobRecord.status == "queued")
        ).scalar_one_or_none()
        if oldest is None:
            return None
        if oldest.tzinfo is None:
            oldest = oldest.replace(tzinfo=timezone.utc)
        return max(0.0, (_utcnow() - oldest).total_seconds())


def oldest_running_age_seconds() -> float | None:
    with create_session() as db:
        oldest = db.execute(
            select(func.min(IngestionJobRecord.started_at)).where(
                IngestionJobRecord.status == "running",
                IngestionJobRecord.started_at.is_not(None),
            )
        ).scalar_one_or_none()
        if oldest is None:
            return None
        if oldest.tzinfo is None:
            oldest = oldest.replace(tzinfo=timezone.utc)
        return max(0.0, (_utcnow() - oldest).total_seconds())


def retrying_count() -> int:
    with create_session() as db:
        count = db.execute(
            select(func.count()).select_from(IngestionJobRecord).where(
                IngestionJobRecord.status.in_(("queued", "running")),
                IngestionJobRecord.attempts > 1,
            )
        ).scalar_one()
        return int(count or 0)


def purge_terminal_jobs() -> int:
    with create_session() as db:
        from datetime import timedelta

        cutoff = _utcnow() - timedelta(seconds=settings.jarvis_completed_ingestion_job_ttl_seconds)
        result = db.execute(
            delete(IngestionJobRecord).where(
                IngestionJobRecord.status.in_(("completed", "failed")),
                IngestionJobRecord.completed_at.is_not(None),
                IngestionJobRecord.completed_at < cutoff,
            )
        )
        db.commit()
        return int(result.rowcount or 0)
