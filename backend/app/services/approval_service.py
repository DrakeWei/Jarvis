import json

from sqlalchemy import select

from app.db.session import create_session
from app.models import ApprovalRecord
from app.schemas.approvals import ApprovalSummary

RUNTIME_PREFIX = "__runtime__:"


def _public_feedback(value: str | None, status: str) -> str | None:
    if status == "pending" and value and value.startswith(RUNTIME_PREFIX):
        return None
    return value


def _runtime_feedback(context: dict[str, object] | None) -> str | None:
    if context is None:
        return None
    return RUNTIME_PREFIX + json.dumps(context, ensure_ascii=True)


def list_approvals(session_id: str | None = None) -> list[ApprovalSummary]:
    with create_session() as db:
        stmt = select(ApprovalRecord).order_by(
            ApprovalRecord.created_at.desc(),
            ApprovalRecord.id.desc(),
        )
        if session_id:
            stmt = stmt.where(ApprovalRecord.session_id == session_id)
        rows = db.scalars(stmt).all()
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
    context: dict[str, object] | None = None,
) -> ApprovalSummary:
    with create_session() as db:
        row = ApprovalRecord(
            session_id=session_id,
            approval_type=approval_type,
            status="pending",
            prompt=prompt,
            feedback=_runtime_feedback(context),
        )
        db.add(row)
        db.commit()
        db.refresh(row)
        return ApprovalSummary(
            id=row.id,
            session_id=row.session_id,
            approval_type=row.approval_type,
            status=row.status,
            prompt=row.prompt,
            feedback=_public_feedback(row.feedback, row.status),
            created_at=row.created_at.isoformat(),
        )


def get_approval(approval_id: int) -> ApprovalSummary | None:
    with create_session() as db:
        row = db.get(ApprovalRecord, approval_id)
        if not row:
            return None
        return ApprovalSummary(
            id=row.id,
            session_id=row.session_id,
            approval_type=row.approval_type,
            status=row.status,
            prompt=row.prompt,
            feedback=_public_feedback(row.feedback, row.status),
            created_at=row.created_at.isoformat(),
        )


def update_approval(approval_id: int, approve: bool, feedback: str) -> ApprovalSummary | None:
    with create_session() as db:
        row = db.get(ApprovalRecord, approval_id)
        if not row:
            return None
        row.status = "approved" if approve else "rejected"
        row.feedback = feedback or None
        db.commit()
        db.refresh(row)
        return ApprovalSummary(
            id=row.id,
            session_id=row.session_id,
            approval_type=row.approval_type,
            status=row.status,
            prompt=row.prompt,
            feedback=_public_feedback(row.feedback, row.status),
            created_at=row.created_at.isoformat(),
        )


def list_pending_runtime_contexts() -> list[tuple[int, str | None, dict[str, object]]]:
    with create_session() as db:
        rows = db.scalars(
            select(ApprovalRecord).where(ApprovalRecord.status == "pending")
        ).all()
        contexts: list[tuple[int, str | None, dict[str, object]]] = []
        for row in rows:
            if not row.feedback or not row.feedback.startswith(RUNTIME_PREFIX):
                continue
            raw = row.feedback[len(RUNTIME_PREFIX):]
            decoded = json.loads(raw)
            if isinstance(decoded, dict):
                contexts.append((row.id, row.session_id, decoded))
        return contexts
