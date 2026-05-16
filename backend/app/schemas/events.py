from datetime import datetime, timezone
from typing import Literal

from pydantic import BaseModel, Field


class SessionCreate(BaseModel):
    title: str = Field(min_length=1, max_length=120)
    workspace_path: str | None = None
    workspace_mode: Literal["bound", "default"] = "bound"


class SessionRename(BaseModel):
    title: str = Field(min_length=1, max_length=120)


class MessageCreate(BaseModel):
    role: Literal["user", "assistant", "system"] = "user"
    content: str = Field(min_length=1)


class SessionSummary(BaseModel):
    session_id: str
    title: str
    workspace_mode: Literal["bound", "default"]
    canonical_workspace_path: str
    workspace_label: str
    workspace_fingerprint: str
    status: str
    created_at: str
    updated_at: str


class TimelineEvent(BaseModel):
    session_id: str
    type: str
    content: str
    created_at: str = Field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )
