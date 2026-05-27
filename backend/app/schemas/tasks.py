from datetime import datetime, timezone

from pydantic import BaseModel, Field


class TaskCreate(BaseModel):
    subject: str = Field(min_length=1, max_length=200)
    description: str = ""
    session_id: str | None = None
    status: str = "pending"
    origin: str | None = None


class TaskSummary(BaseModel):
    id: int
    session_id: str | None = None
    subject: str
    description: str
    status: str
    title: str | None = None
    summary: str | None = None
    origin: str | None = None
    owner: str | None = None
    updated_at: str | None = None
    activated_at: str | None = None
    suspended_at: str | None = None
    completed_at: str | None = None
    created_at: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
