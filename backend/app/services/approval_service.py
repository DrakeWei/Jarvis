import json

from sqlalchemy import select, update

from app.db.session import create_session
from app.models import ApprovalRecord, SessionRecord
from app.schemas.approvals import ApprovalSummary
import app.services.checkpoint_service as checkpoint_service

RUNTIME_PREFIX = "__runtime__:"


def _public_feedback(value: str | None, status: str) -> str | None:
    if status == "pending" and value and value.startswith(RUNTIME_PREFIX):
        return None
    return value


def _runtime_feedback(context: dict[str, object] | None) -> str | None:
    if context is None:
        return None
    return RUNTIME_PREFIX + json.dumps(context, ensure_ascii=True)


def list_approvals(session_id: str | None = None, *, branch_context_id: str | None = None) -> list[ApprovalSummary]:
    with create_session() as db:
        if session_id and branch_context_id is None:
            session_row = db.get(SessionRecord, session_id)
            branch_context_id = session_row.branch_context_id if session_row else None
        stmt = (
            select(
                ApprovalRecord.id,
                ApprovalRecord.session_id,
                ApprovalRecord.approval_type,
                ApprovalRecord.status,
                ApprovalRecord.prompt,
                ApprovalRecord.feedback,
                ApprovalRecord.created_at,
            )
            .order_by(ApprovalRecord.created_at.desc(), ApprovalRecord.id.desc())
        )
        if session_id:
            stmt = stmt.where(ApprovalRecord.session_id == session_id)
        if branch_context_id is not None:
            stmt = stmt.where(ApprovalRecord.branch_context_id == branch_context_id)
        rows = db.execute(stmt).all()
        return [
            ApprovalSummary(
                id=row.id,
                session_id=row.session_id,
                approval_type=row.approval_type,
                status=row.status,
                prompt=row.prompt,
                feedback=_public_feedback(row.feedback, row.status),
                created_at=row.created_at.isoformat(),
            )
            for row in rows
        ]


def create_approval(
    session_id: str,
    approval_type: str,
    prompt: str,
    *,
    task_id: int | None = None,
    turn_id: int | None = None,
    checkpoint_id: int | None = None,
    context: dict[str, object] | None = None,
) -> ApprovalSummary:
    with create_session() as db:
        session_row = db.get(SessionRecord, session_id)
        row = ApprovalRecord(
            session_id=session_id,
            task_id=task_id,
            branch_context_id=session_row.branch_context_id if session_row else None,
            turn_id=turn_id,
            checkpoint_id=checkpoint_id,
            approval_type=approval_type,
            status="pending",
            prompt=prompt,
            feedback=_runtime_feedback(context) if checkpoint_id is None else None,
        )
        db.add(row)
        db.commit()
        db.refresh(row)
        return _to_summary(row)


def get_approval(approval_id: int) -> ApprovalSummary | None:
    with create_session() as db:
        row = db.get(ApprovalRecord, approval_id)
        return _to_summary(row) if row else None


def _to_summary(row: ApprovalRecord) -> ApprovalSummary:
    return ApprovalSummary(
        id=row.id,
        session_id=row.session_id,
        approval_type=row.approval_type,
        status=row.status,
        prompt=row.prompt,
        feedback=_public_feedback(row.feedback, row.status),
        created_at=row.created_at.isoformat(),
    )


def _runtime_context_from_row(row: ApprovalRecord) -> dict[str, object] | None:
    if row.checkpoint_id:
        decoded = checkpoint_service.get_checkpoint(row.checkpoint_id)
        return decoded if isinstance(decoded, dict) else None
    if not row.feedback or not row.feedback.startswith(RUNTIME_PREFIX):
        return None
    raw = row.feedback[len(RUNTIME_PREFIX):]
    decoded = json.loads(raw)
    return decoded if isinstance(decoded, dict) else None


def apply_approval_decision(approval_id: int, approve: bool, feedback: str) -> tuple[ApprovalSummary | None, bool]:
    decided_status = "approved" if approve else "rejected"
    with create_session() as db:
        updated = db.execute(
            update(ApprovalRecord)
            .where(
                ApprovalRecord.id == approval_id,
                ApprovalRecord.status == "pending",
            )
            .values(
                status=decided_status,
                feedback=feedback or None,
            )
        )
        changed = int(updated.rowcount or 0) > 0
        if changed:
            db.commit()
        row = db.get(ApprovalRecord, approval_id)
        if row is None:
            if changed:
                db.rollback()
            return None, False
        if not changed:
            db.rollback()
        return _to_summary(row), changed


def approval_matches_latest_checkpoint(approval_id: int) -> bool:
    with create_session() as db:
        row = db.get(ApprovalRecord, approval_id)
        if row is None:
            return False
        if row.turn_id is None or row.checkpoint_id is None:
            return True
        turn_id = row.turn_id
        checkpoint_id = row.checkpoint_id
    latest = checkpoint_service.latest_checkpoint(turn_id)
    return latest is not None and latest.id == checkpoint_id


def get_approval_turn_metadata(approval_id: int) -> tuple[str | None, int | None, int | None, str | None]:
    with create_session() as db:
        row = db.get(ApprovalRecord, approval_id)
        if row is None:
            return None, None, None, None
        return row.session_id, row.turn_id, row.checkpoint_id, row.approval_type


def reject_superseded_turn_approvals(
    *,
    turn_id: int,
    approval_type: str,
    keep_approval_id: int,
    feedback: str = "Superseded by a newer approval request.",
) -> int:
    with create_session() as db:
        updated = db.execute(
            update(ApprovalRecord)
            .where(
                ApprovalRecord.turn_id == turn_id,
                ApprovalRecord.approval_type == approval_type,
                ApprovalRecord.status == "pending",
                ApprovalRecord.id != keep_approval_id,
            )
            .values(
                status="rejected",
                feedback=feedback,
            )
        )
        changed = int(updated.rowcount or 0)
        if changed:
            db.commit()
        else:
            db.rollback()
        return changed


def get_pending_runtime_context(approval_id: int) -> tuple[str | None, dict[str, object]] | None:
    with create_session() as db:
        row = db.get(ApprovalRecord, approval_id)
        if not row or row.status != "pending":
            return None
        decoded = _runtime_context_from_row(row)
        return (row.session_id, decoded) if isinstance(decoded, dict) else None
