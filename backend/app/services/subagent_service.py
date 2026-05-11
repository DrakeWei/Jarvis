from sqlalchemy import select

from app.db.session import create_session
from app.models import AgentMessageRecord, AgentRecord
from app.schemas.subagents import SubagentSummary


def list_subagents(session_id: str | None = None) -> list[SubagentSummary]:
    with create_session() as db:
        stmt = select(AgentRecord).where(AgentRecord.kind == "subagent").order_by(
            AgentRecord.created_at.desc(),
            AgentRecord.id.desc(),
        )
        if session_id:
            stmt = stmt.where(AgentRecord.session_id == session_id)
        rows = db.scalars(stmt).all()
        return [
            SubagentSummary(
                id=row.id,
                session_id=row.session_id,
                name=row.name,
                role=row.role,
                kind=row.kind,
                status=row.status,
                created_at=row.created_at.isoformat(),
            )
            for row in rows
        ]


def create_subagent(session_id: str, name: str) -> SubagentSummary:
    with create_session() as db:
        row = AgentRecord(
            session_id=session_id,
            name=name,
            role="Explorer",
            kind="subagent",
            status="running",
        )
        db.add(row)
        db.commit()
        db.refresh(row)
        return SubagentSummary(
            id=row.id,
            session_id=row.session_id,
            name=row.name,
            role=row.role,
            kind=row.kind,
            status=row.status,
            created_at=row.created_at.isoformat(),
        )


def complete_subagent(agent_id: int) -> SubagentSummary | None:
    with create_session() as db:
        row = db.get(AgentRecord, agent_id)
        if not row or row.kind != "subagent":
            return None
        row.status = "completed"
        db.commit()
        db.refresh(row)
        return SubagentSummary(
            id=row.id,
            session_id=row.session_id,
            name=row.name,
            role=row.role,
            kind=row.kind,
            status=row.status,
            created_at=row.created_at.isoformat(),
        )


def add_subagent_summary(agent_id: int, content: str) -> None:
    with create_session() as db:
        row = AgentMessageRecord(
            agent_id=agent_id,
            direction="summary",
            content=content,
        )
        db.add(row)
        db.commit()


def get_subagent_summary(agent_id: int) -> str | None:
    with create_session() as db:
        row = db.scalars(
            select(AgentMessageRecord)
            .where(
                AgentMessageRecord.agent_id == agent_id,
                AgentMessageRecord.direction == "summary",
            )
            .order_by(AgentMessageRecord.id.desc())
        ).first()
        return row.content if row else None
