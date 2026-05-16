from datetime import datetime, timezone

from pydantic import BaseModel, Field


class SessionMemorySummary(BaseModel):
    id: int
    session_id: str
    kind: str
    content: str
    source_turn_id: int | None
    path_ref: str | None
    salience: int
    status: str
    created_at: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    updated_at: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
