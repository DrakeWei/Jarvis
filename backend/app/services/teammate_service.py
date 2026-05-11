from sqlalchemy import select

from app.db.session import create_session
from app.models import AgentMessageRecord, AgentRecord
from app.schemas.teammates import (
    TeammateCreate,
    TeammateMessageSummary,
    TeammateSummary,
)


def list_teammates(session_id: str | None = None) -> list[TeammateSummary]:
    with create_session() as db:
        stmt = select(AgentRecord).where(AgentRecord.kind == "teammate").order_by(
            AgentRecord.created_at.desc(),
            AgentRecord.id.desc(),
        )
        if session_id:
            stmt = stmt.where(AgentRecord.session_id == session_id)
        rows = db.scalars(stmt).all()
        return [
            TeammateSummary(
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


def create_teammate(payload: TeammateCreate) -> TeammateSummary:
    with create_session() as db:
        row = AgentRecord(
            session_id=payload.session_id,
            name=payload.name,
            role=payload.role,
            kind="teammate",
            status="idle",
        )
        db.add(row)
        db.commit()
        db.refresh(row)
        return TeammateSummary(
            id=row.id,
            session_id=row.session_id,
            name=row.name,
            role=row.role,
            kind=row.kind,
            status=row.status,
            created_at=row.created_at.isoformat(),
        )


def get_teammate(agent_id: int) -> TeammateSummary | None:
    with create_session() as db:
        row = db.get(AgentRecord, agent_id)
        if not row or row.kind != "teammate":
            return None
        return TeammateSummary(
            id=row.id,
            session_id=row.session_id,
            name=row.name,
            role=row.role,
            kind=row.kind,
            status=row.status,
            created_at=row.created_at.isoformat(),
        )


def update_teammate_status(agent_id: int, status: str) -> TeammateSummary | None:
    with create_session() as db:
        row = db.get(AgentRecord, agent_id)
        if not row or row.kind != "teammate":
            return None
        row.status = status
        db.commit()
        db.refresh(row)
        return TeammateSummary(
            id=row.id,
            session_id=row.session_id,
            name=row.name,
            role=row.role,
            kind=row.kind,
            status=row.status,
            created_at=row.created_at.isoformat(),
        )


def add_teammate_message(agent_id: int, direction: str, content: str) -> TeammateMessageSummary:
    with create_session() as db:
        row = AgentMessageRecord(
            agent_id=agent_id,
            direction=direction,
            content=content,
        )
        db.add(row)
        db.commit()
        db.refresh(row)
        return TeammateMessageSummary(
            id=row.id,
            agent_id=row.agent_id,
            direction=row.direction,
            content=row.content,
            created_at=row.created_at.isoformat(),
        )


def list_teammate_messages(agent_id: int) -> list[TeammateMessageSummary]:
    with create_session() as db:
        rows = db.scalars(
            select(AgentMessageRecord)
            .where(AgentMessageRecord.agent_id == agent_id)
            .order_by(AgentMessageRecord.created_at.desc(), AgentMessageRecord.id.desc())
        ).all()
        return [
            TeammateMessageSummary(
                id=row.id,
                agent_id=row.agent_id,
                direction=row.direction,
                content=row.content,
                created_at=row.created_at.isoformat(),
            )
            for row in rows
        ]
