from pydantic import BaseModel, Field

from app.schemas.events import SessionSummary


class GitBranchListSummary(BaseModel):
    current_branch: str | None
    branches: list[str] = Field(default_factory=list)


class GitBranchSwitchRequest(BaseModel):
    branch_name: str = Field(min_length=1, max_length=160)


class GitBranchSwitchResult(BaseModel):
    session: SessionSummary
    source_branch: str | None
    target_branch: str | None
    created_new_branch: bool = False
