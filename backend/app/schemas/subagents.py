from datetime import datetime, timezone
from pydantic import BaseModel, Field


class SubagentRunCreate(BaseModel):
    session_id: str
    name: str = Field(min_length=1, max_length=120)
    prompt: str = Field(min_length=1)


class SubagentSummary(BaseModel):
    id: int
    session_id: str | None
    name: str
    role: str
    kind: str
    status: str
    created_at: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())


class SubagentResult(BaseModel):
    subagent: SubagentSummary
    summary: str
