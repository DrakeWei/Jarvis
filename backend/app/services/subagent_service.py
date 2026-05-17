from sqlalchemy import select

from app.db.session import create_session
from app.models import AgentMessageRecord, AgentRecord
from app.schemas.subagents import SubagentSummary


def _to_subagent_summary(row: AgentRecord) -> SubagentSummary:
    return SubagentSummary(
        id=row.id,
        session_id=row.session_id,
        name=row.name,
        role=row.role,
        kind=row.kind,
        status=row.status,
        base_workspace_path=row.base_workspace_path,
        execution_workspace_path=row.execution_workspace_path,
        isolation_mode=row.isolation_mode,
        git_branch=row.git_branch,
        git_base_revision=row.git_base_revision,
        cleanup_status=row.cleanup_status,
        preserved_reason=row.preserved_reason,
        created_at=row.created_at.isoformat(),
    )


def list_subagents(session_id: str | None = None) -> list[SubagentSummary]:
    with create_session() as db:
        stmt = select(AgentRecord).where(AgentRecord.kind == "subagent").order_by(AgentRecord.created_at.desc(), AgentRecord.id.desc())
        if session_id:
            stmt = stmt.where(AgentRecord.session_id == session_id)
        rows = db.scalars(stmt).all()
        return [_to_subagent_summary(row) for row in rows]


def create_subagent(
    session_id: str,
    name: str,
    *,
    base_workspace_path: str | None,
    isolation_mode: str,
) -> SubagentSummary:
    with create_session() as db:
        row = AgentRecord(
            session_id=session_id,
            name=name,
            role="Explorer",
            kind="subagent",
            status="running",
            base_workspace_path=base_workspace_path,
            execution_workspace_path=base_workspace_path if isolation_mode == "shared" else None,
            isolation_mode=isolation_mode,
            cleanup_status="pending",
        )
        db.add(row)
        db.commit()
        db.refresh(row)
        return _to_subagent_summary(row)


def update_subagent_execution(
    agent_id: int,
    *,
    execution_workspace_path: str | None = None,
    git_branch: str | None = None,
    git_base_revision: str | None = None,
    cleanup_status: str | None = None,
    preserved_reason: str | None = None,
) -> SubagentSummary | None:
    with create_session() as db:
        row = db.get(AgentRecord, agent_id)
        if not row or row.kind != "subagent":
            return None
        if execution_workspace_path is not None:
            row.execution_workspace_path = execution_workspace_path
        if git_branch is not None:
            row.git_branch = git_branch
        if git_base_revision is not None:
            row.git_base_revision = git_base_revision
        if cleanup_status is not None:
            row.cleanup_status = cleanup_status
        row.preserved_reason = preserved_reason
        db.commit()
        db.refresh(row)
        return _to_subagent_summary(row)


def finish_subagent(
    agent_id: int,
    *,
    status: str,
    execution_workspace_path: str | None = None,
    git_branch: str | None = None,
    git_base_revision: str | None = None,
    cleanup_status: str | None = None,
    preserved_reason: str | None = None,
) -> SubagentSummary | None:
    with create_session() as db:
        row = db.get(AgentRecord, agent_id)
        if not row or row.kind != "subagent":
            return None
        row.status = status
        if execution_workspace_path is not None:
            row.execution_workspace_path = execution_workspace_path
        if git_branch is not None:
            row.git_branch = git_branch
        if git_base_revision is not None:
            row.git_base_revision = git_base_revision
        if cleanup_status is not None:
            row.cleanup_status = cleanup_status
        row.preserved_reason = preserved_reason
        db.commit()
        db.refresh(row)
        return _to_subagent_summary(row)


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
        row = db.execute(
            select(AgentMessageRecord.content)
            .where(
                AgentMessageRecord.agent_id == agent_id,
                AgentMessageRecord.direction == "summary",
            )
            .order_by(AgentMessageRecord.id.desc())
            .limit(1)
        ).first()
        return row.content if row else None
