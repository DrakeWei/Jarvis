from datetime import datetime, timezone
from pydantic import BaseModel, Field


class SubagentRunCreate(BaseModel):
    session_id: str
    name: str = Field(min_length=1, max_length=120)
    prompt: str = Field(min_length=1)
    isolation_mode: str = Field(default="shared", pattern="^(shared|worktree)$")


class SubagentSummary(BaseModel):
    id: int
    session_id: str | None
    name: str
    role: str
    kind: str
    status: str
    base_workspace_path: str | None = None
    execution_workspace_path: str | None = None
    isolation_mode: str = "shared"
    git_branch: str | None = None
    git_base_revision: str | None = None
    cleanup_status: str = "pending"
    preserved_reason: str | None = None
    created_at: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())


class SubagentResult(BaseModel):
    subagent: SubagentSummary
    summary: str
