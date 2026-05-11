from datetime import datetime, timezone

from pydantic import BaseModel, Field


class ApprovalSummary(BaseModel):
    id: int
    session_id: str | None
    approval_type: str
    status: str
    prompt: str
    feedback: str | None = None
    created_at: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())


class ApprovalDecision(BaseModel):
    approve: bool
    feedback: str = ""
