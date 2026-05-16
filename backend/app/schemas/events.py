from datetime import datetime, timezone
from typing import Literal

from pydantic import BaseModel, Field, model_validator


class SessionCreate(BaseModel):
    title: str = Field(min_length=1, max_length=120)
    workspace_path: str | None = None
    workspace_mode: Literal["bound", "default"] = "bound"


class SessionRename(BaseModel):
    title: str = Field(min_length=1, max_length=120)


class MessageCreate(BaseModel):
    role: Literal["user", "assistant", "system"] = "user"
    content: str = ""
    asset_ids: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_message_payload(self) -> "MessageCreate":
        self.content = self.content.strip()
        self.asset_ids = [asset_id.strip() for asset_id in self.asset_ids if asset_id and asset_id.strip()]
        if not self.content and not self.asset_ids:
            raise ValueError("A message must include text or at least one asset reference.")
        return self


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
