from datetime import datetime, timezone

from pydantic import BaseModel, Field


class ExecutionLeaseSummary(BaseModel):
    id: int
    scope_type: str
    scope_key: str
    owner_id: str
    status: str
    acquired_at: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    heartbeat_at: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    expires_at: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
