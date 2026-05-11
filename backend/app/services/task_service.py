from sqlalchemy import select

from app.db.session import create_session
from app.models import TaskRecord
from app.schemas.tasks import TaskCreate, TaskSummary


def list_tasks() -> list[TaskSummary]:
    with create_session() as db:
        rows = db.scalars(select(TaskRecord).order_by(TaskRecord.created_at.desc(), TaskRecord.id.desc())).all()
        return [
            TaskSummary(
                id=row.id,
                subject=row.subject,
                description=row.description,
                status=row.status,
                owner=row.owner,
                created_at=row.created_at.isoformat(),
            )
            for row in rows
        ]


def create_task(payload: TaskCreate) -> TaskSummary:
    with create_session() as db:
        row = TaskRecord(
            session_id=payload.session_id,
            subject=payload.subject,
            description=payload.description,
        )
        db.add(row)
        db.commit()
        db.refresh(row)
        return TaskSummary(
            id=row.id,
            subject=row.subject,
            description=row.description,
            status=row.status,
            owner=row.owner,
            created_at=row.created_at.isoformat(),
        )
