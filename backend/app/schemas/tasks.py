from datetime import datetime, timezone

from pydantic import BaseModel, Field


class TaskCreate(BaseModel):
    subject: str = Field(min_length=1, max_length=200)
    description: str = ""
    session_id: str | None = None


class TaskSummary(BaseModel):
    id: int
    subject: str
    description: str
    status: str
    owner: str | None = None
    created_at: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
