from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlalchemy import or_, select, update
from sqlalchemy.exc import IntegrityError

from app.core.config import settings
from app.db.session import create_session
from app.models import ExecutionLeaseRecord
from app.schemas.leases import ExecutionLeaseSummary


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _as_utc(value: datetime) -> datetime:
    return value if value.tzinfo is not None else value.replace(tzinfo=timezone.utc)


def _to_summary(row: ExecutionLeaseRecord) -> ExecutionLeaseSummary:
    return ExecutionLeaseSummary(
        id=row.id,
        scope_type=row.scope_type,
        scope_key=row.scope_key,
        owner_id=row.owner_id,
        status=row.status,
        acquired_at=_as_utc(row.acquired_at).isoformat(),
        heartbeat_at=_as_utc(row.heartbeat_at).isoformat(),
        expires_at=_as_utc(row.expires_at).isoformat(),
    )


def _claimable(now: datetime, owner_id: str):
    return or_(
        ExecutionLeaseRecord.status != "active",
        ExecutionLeaseRecord.expires_at <= now,
        ExecutionLeaseRecord.owner_id == owner_id,
    )


def try_acquire(scope_type: str, scope_key: str, owner_id: str, *, ttl_seconds: int | None = None) -> bool:
    ttl = max(1, ttl_seconds or settings.jarvis_execution_lease_ttl_seconds)
    now = _utcnow()
    expires_at = now + timedelta(seconds=ttl)
    values = {
        "owner_id": owner_id,
        "status": "active",
        "acquired_at": now,
        "heartbeat_at": now,
        "expires_at": expires_at,
    }
    with create_session() as db:
        claimed = db.execute(
            update(ExecutionLeaseRecord)
            .where(
                ExecutionLeaseRecord.scope_type == scope_type,
                ExecutionLeaseRecord.scope_key == scope_key,
                _claimable(now, owner_id),
            )
            .values(**values)
        )
        if int(claimed.rowcount or 0) > 0:
            db.commit()
            return True
        try:
            db.add(ExecutionLeaseRecord(scope_type=scope_type, scope_key=scope_key, **values))
            db.commit()
            return True
        except IntegrityError:
            db.rollback()
        claimed = db.execute(
            update(ExecutionLeaseRecord)
            .where(
                ExecutionLeaseRecord.scope_type == scope_type,
                ExecutionLeaseRecord.scope_key == scope_key,
                _claimable(now, owner_id),
            )
            .values(**values)
        )
        if int(claimed.rowcount or 0) > 0:
            db.commit()
            return True
        return False


def release(scope_type: str, scope_key: str, owner_id: str) -> bool:
    now = _utcnow()
    with create_session() as db:
        released = db.execute(
            update(ExecutionLeaseRecord)
            .where(
                ExecutionLeaseRecord.scope_type == scope_type,
                ExecutionLeaseRecord.scope_key == scope_key,
                ExecutionLeaseRecord.owner_id == owner_id,
                ExecutionLeaseRecord.status == "active",
            )
            .values(status="released", heartbeat_at=now, expires_at=now)
        )
        if int(released.rowcount or 0) == 0:
            return False
        db.commit()
        return True


def renew(scope_type: str, scope_key: str, owner_id: str, *, ttl_seconds: int | None = None) -> bool:
    ttl = max(1, ttl_seconds or settings.jarvis_execution_lease_ttl_seconds)
    now = _utcnow()
    with create_session() as db:
        renewed = db.execute(
            update(ExecutionLeaseRecord)
            .where(
                ExecutionLeaseRecord.scope_type == scope_type,
                ExecutionLeaseRecord.scope_key == scope_key,
                ExecutionLeaseRecord.owner_id == owner_id,
                ExecutionLeaseRecord.status == "active",
                ExecutionLeaseRecord.expires_at > now,
            )
            .values(heartbeat_at=now, expires_at=now + timedelta(seconds=ttl))
        )
        if int(renewed.rowcount or 0) == 0:
            return False
        db.commit()
        return True


def is_active(scope_type: str, scope_key: str) -> bool:
    with create_session() as db:
        row = db.scalars(
            select(ExecutionLeaseRecord)
            .where(ExecutionLeaseRecord.scope_type == scope_type, ExecutionLeaseRecord.scope_key == scope_key)
            .limit(1)
        ).first()
        return bool(row and row.status == "active" and _as_utc(row.expires_at) > _utcnow())


def list_leases(*, scope_type: str | None = None, status: str | None = None) -> list[ExecutionLeaseSummary]:
    with create_session() as db:
        stmt = select(ExecutionLeaseRecord).order_by(
            ExecutionLeaseRecord.acquired_at.desc(),
            ExecutionLeaseRecord.id.desc(),
        )
        if scope_type:
            stmt = stmt.where(ExecutionLeaseRecord.scope_type == scope_type)
        if status:
            stmt = stmt.where(ExecutionLeaseRecord.status == status)
        rows = db.scalars(stmt).all()
        return [_to_summary(row) for row in rows]


def force_release(lease_id: int, *, reason_owner_id: str = "operator") -> ExecutionLeaseSummary | None:
    with create_session() as db:
        row = db.get(ExecutionLeaseRecord, lease_id)
        if row is None:
            return None
        now = _utcnow()
        row.status = "released"
        row.owner_id = reason_owner_id
        row.heartbeat_at = now
        row.expires_at = now
        db.commit()
        db.refresh(row)
        return _to_summary(row)
