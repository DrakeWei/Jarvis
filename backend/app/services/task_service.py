from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import select

from app.db.session import create_session
from app.models import TaskClassificationRecord, TaskRecord, TaskStateTransitionRecord
from app.schemas.tasks import TaskCreate, TaskSummary


TASK_STATUS_ACTIVE = "active"
TASK_STATUS_SUSPENDED = "suspended"
TASK_STATUS_COMPLETED = "completed"
TASK_STATUS_FAILED = "failed"
TASK_STATUS_CANCELLED = "cancelled"
TASK_TERMINAL_STATUSES = {TASK_STATUS_COMPLETED, TASK_STATUS_FAILED, TASK_STATUS_CANCELLED}


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _normalize_text(value: str) -> str:
    return " ".join(value.strip().split())


def _task_title(subject: str, title: str | None) -> str:
    normalized = _normalize_text(title or subject)
    return normalized[:200] if normalized else "Untitled task"


def _task_summary(description: str, summary: str | None) -> str:
    normalized = _normalize_text(summary or description)
    return normalized[:1000]


def _to_summary(row: TaskRecord) -> TaskSummary:
    return TaskSummary(
        id=row.id,
        session_id=row.session_id,
        subject=row.subject,
        description=row.description,
        status=row.status,
        title=_task_title(row.subject, row.title),
        summary=_task_summary(row.description, row.summary),
        origin=row.origin,
        owner=row.owner,
        updated_at=row.updated_at.isoformat() if row.updated_at else None,
        activated_at=row.activated_at.isoformat() if row.activated_at else None,
        suspended_at=row.suspended_at.isoformat() if row.suspended_at else None,
        completed_at=row.completed_at.isoformat() if row.completed_at else None,
        created_at=row.created_at.isoformat(),
    )


def _derive_subject(text: str) -> str:
    normalized = _normalize_text(text)
    if not normalized:
        return "Untitled task"
    return normalized[:200]


def list_tasks(session_id: str | None = None) -> list[TaskSummary]:
    try:
        with create_session() as db:
            stmt = select(TaskRecord).order_by(TaskRecord.updated_at.desc(), TaskRecord.id.desc())
            if session_id:
                stmt = stmt.where(TaskRecord.session_id == session_id)
            rows = db.scalars(stmt).all()
            return [_to_summary(row) for row in rows]
    except Exception:
        return []


def get_task(task_id: int) -> TaskSummary | None:
    try:
        with create_session() as db:
            row = db.get(TaskRecord, task_id)
            return _to_summary(row) if row else None
    except Exception:
        return None


def get_active_task(session_id: str) -> TaskSummary | None:
    try:
        with create_session() as db:
            row = db.scalars(
                select(TaskRecord)
                .where(TaskRecord.session_id == session_id, TaskRecord.status == TASK_STATUS_ACTIVE)
                .order_by(TaskRecord.activated_at.desc(), TaskRecord.updated_at.desc(), TaskRecord.id.desc())
                .limit(1)
            ).first()
            return _to_summary(row) if row else None
    except Exception:
        return None


def get_most_recent_suspended_task(session_id: str) -> TaskSummary | None:
    try:
        with create_session() as db:
            row = db.scalars(
                select(TaskRecord)
                .where(TaskRecord.session_id == session_id, TaskRecord.status == TASK_STATUS_SUSPENDED)
                .order_by(TaskRecord.suspended_at.desc(), TaskRecord.updated_at.desc(), TaskRecord.id.desc())
                .limit(1)
            ).first()
            return _to_summary(row) if row else None
    except Exception:
        return None


def list_session_tasks(
    session_id: str,
    *,
    statuses: tuple[str, ...] | None = None,
    limit: int = 50,
) -> list[TaskSummary]:
    try:
        with create_session() as db:
            stmt = select(TaskRecord).where(TaskRecord.session_id == session_id)
            if statuses:
                stmt = stmt.where(TaskRecord.status.in_(statuses))
            stmt = stmt.order_by(TaskRecord.updated_at.desc(), TaskRecord.id.desc()).limit(limit)
            rows = db.scalars(stmt).all()
            return [_to_summary(row) for row in rows]
    except Exception:
        return []


def create_task(payload: TaskCreate) -> TaskSummary:
    with create_session() as db:
        now = _utcnow()
        title = _task_title(payload.subject, None)
        summary = _task_summary(payload.description, None)
        row = TaskRecord(
            session_id=payload.session_id,
            subject=title,
            description=payload.description,
            status=payload.status,
            title=title,
            summary=summary,
            origin=payload.origin,
            updated_at=now,
            activated_at=now if payload.status == TASK_STATUS_ACTIVE else None,
            suspended_at=now if payload.status == TASK_STATUS_SUSPENDED else None,
            completed_at=now if payload.status in TASK_TERMINAL_STATUSES else None,
        )
        db.add(row)
        db.commit()
        db.refresh(row)
        return _to_summary(row)


def _record_transition(
    db,
    *,
    task_id: int,
    session_id: str | None,
    from_status: str | None,
    to_status: str,
    reason: str | None,
    trigger_message_id: int | None = None,
    trigger_turn_id: int | None = None,
) -> None:
    db.add(
        TaskStateTransitionRecord(
            task_id=task_id,
            session_id=session_id,
            from_status=from_status,
            to_status=to_status,
            reason=reason,
            trigger_message_id=trigger_message_id,
            trigger_turn_id=trigger_turn_id,
        )
    )


def _apply_status(row: TaskRecord, status: str, *, now: datetime) -> None:
    row.status = status
    row.updated_at = now
    if status == TASK_STATUS_ACTIVE:
        row.activated_at = now
        row.suspended_at = None
    elif status == TASK_STATUS_SUSPENDED:
        row.suspended_at = now
    elif status in TASK_TERMINAL_STATUSES:
        row.completed_at = now


def create_runtime_task(
    session_id: str,
    *,
    title: str,
    summary: str = "",
    origin: str = "user_request",
    status: str = TASK_STATUS_ACTIVE,
) -> TaskSummary:
    payload = TaskCreate(
        session_id=session_id,
        subject=title,
        description=summary,
        status=status,
        origin=origin,
    )
    return create_task(payload)


def set_task_status(
    task_id: int,
    status: str,
    *,
    reason: str | None = None,
    trigger_message_id: int | None = None,
    trigger_turn_id: int | None = None,
) -> TaskSummary | None:
    with create_session() as db:
        row = db.get(TaskRecord, task_id)
        if row is None:
            return None
        previous = row.status
        now = _utcnow()
        _apply_status(row, status, now=now)
        _record_transition(
            db,
            task_id=row.id,
            session_id=row.session_id,
            from_status=previous,
            to_status=status,
            reason=reason,
            trigger_message_id=trigger_message_id,
            trigger_turn_id=trigger_turn_id,
        )
        db.commit()
        db.refresh(row)
        return _to_summary(row)


def update_task_summary(task_id: int, *, title: str | None = None, summary: str | None = None) -> TaskSummary | None:
    with create_session() as db:
        row = db.get(TaskRecord, task_id)
        if row is None:
            return None
        if title is not None and _normalize_text(title):
            normalized_title = _task_title(_normalize_text(title), None)
            row.title = normalized_title
            row.subject = normalized_title
        if summary is not None:
            normalized_summary = _task_summary(summary, None)
            row.summary = normalized_summary
            row.description = normalized_summary
        row.updated_at = _utcnow()
        db.commit()
        db.refresh(row)
        return _to_summary(row)


def record_classification(
    *,
    session_id: str,
    message_id: int | None,
    active_task_id: int | None,
    decision: str,
    target_task_id: int | None,
    confidence: int,
    rationale_json: str,
) -> None:
    with create_session() as db:
        db.add(
            TaskClassificationRecord(
                session_id=session_id,
                message_id=message_id,
                active_task_id=active_task_id,
                decision=decision,
                target_task_id=target_task_id,
                confidence=max(0, min(100, int(confidence))),
                rationale_json=rationale_json or "{}",
            )
        )
        db.commit()


def apply_routing_decision(
    session_id: str,
    *,
    decision: str,
    content: str,
    target_task_id: int | None = None,
    reason: str | None = None,
    origin: str = "classifier_split",
    trigger_message_id: int | None = None,
) -> TaskSummary:
    with create_session() as db:
        now = _utcnow()
        active = db.scalars(
            select(TaskRecord)
            .where(TaskRecord.session_id == session_id, TaskRecord.status == TASK_STATUS_ACTIVE)
            .order_by(TaskRecord.activated_at.desc(), TaskRecord.updated_at.desc(), TaskRecord.id.desc())
            .limit(1)
        ).first()

        if decision == "continue_active_task" and active is not None:
            active.updated_at = now
            db.commit()
            db.refresh(active)
            return _to_summary(active)

        if decision == "resume_suspended_task" and target_task_id is not None:
            target = db.get(TaskRecord, target_task_id)
            if target is not None and target.session_id == session_id and target.status == TASK_STATUS_SUSPENDED:
                if active is not None and active.id != target.id:
                    previous = active.status
                    _apply_status(active, TASK_STATUS_SUSPENDED, now=now)
                    _record_transition(
                        db,
                        task_id=active.id,
                        session_id=session_id,
                        from_status=previous,
                        to_status=TASK_STATUS_SUSPENDED,
                        reason=reason or "resumed_another_task",
                        trigger_message_id=trigger_message_id,
                    )
                previous = target.status
                _apply_status(target, TASK_STATUS_ACTIVE, now=now)
                _record_transition(
                    db,
                    task_id=target.id,
                    session_id=session_id,
                    from_status=previous,
                    to_status=TASK_STATUS_ACTIVE,
                    reason=reason or "resume_suspended_task",
                    trigger_message_id=trigger_message_id,
                )
                db.commit()
                db.refresh(target)
                return _to_summary(target)

        if active is not None:
            previous = active.status
            _apply_status(active, TASK_STATUS_SUSPENDED, now=now)
            _record_transition(
                db,
                task_id=active.id,
                session_id=session_id,
                from_status=previous,
                to_status=TASK_STATUS_SUSPENDED,
                reason=reason or "new_task_started",
                trigger_message_id=trigger_message_id,
            )

        title = _derive_subject(content)
        summary = _task_summary(content, None)
        created = TaskRecord(
            session_id=session_id,
            subject=title,
            description=summary,
            status=TASK_STATUS_ACTIVE,
            title=title,
            summary=summary,
            origin=origin,
            updated_at=now,
            activated_at=now,
        )
        db.add(created)
        db.flush()
        _record_transition(
            db,
            task_id=created.id,
            session_id=session_id,
            from_status=None,
            to_status=TASK_STATUS_ACTIVE,
            reason=reason or "create_new_task",
            trigger_message_id=trigger_message_id,
        )
        db.commit()
        db.refresh(created)
        return _to_summary(created)
