from sqlalchemy import select

from app.db.session import create_session
from app.models import ApprovalRecord
from app.schemas.approvals import ApprovalSummary


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
                feedback=row.feedback,
                created_at=row.created_at.isoformat(),
            )
            for row in rows
        ]


def create_approval(session_id: str, approval_type: str, prompt: str) -> ApprovalSummary:
    with create_session() as db:
        row = ApprovalRecord(
            session_id=session_id,
            approval_type=approval_type,
            status="pending",
            prompt=prompt,
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
            feedback=row.feedback,
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
            feedback=row.feedback,
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
            feedback=row.feedback,
            created_at=row.created_at.isoformat(),
        )
