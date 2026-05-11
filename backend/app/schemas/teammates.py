from datetime import datetime, timezone

from pydantic import BaseModel, Field


class TeammateCreate(BaseModel):
    session_id: str
    name: str = Field(min_length=1, max_length=120)
    role: str = Field(min_length=1, max_length=120)


class TeammateSummary(BaseModel):
    id: int
    session_id: str | None
    name: str
    role: str
    kind: str
    status: str
    created_at: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())


class TeammateMessageCreate(BaseModel):
    content: str = Field(min_length=1)


class TeammateMessageSummary(BaseModel):
    id: int
    agent_id: int
    direction: str
    content: str
    created_at: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
