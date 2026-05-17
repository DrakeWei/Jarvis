from datetime import datetime, timezone

from pydantic import BaseModel, Field


class BackgroundJobSummary(BaseModel):
    id: int
    session_id: str | None
    job_type: str
    command: str
    status: str
    owner_id: str | None
    attempts: int
    next_attempt_at: str | None = None
    output_text: str | None = None
    created_at: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    updated_at: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    started_at: str | None = None
    completed_at: str | None = None
