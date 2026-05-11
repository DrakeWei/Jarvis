from datetime import datetime, timezone

from pydantic import BaseModel, Field


class ToolExecutionSummary(BaseModel):
    id: int
    session_id: str
    tool_name: str
    status: str
    input_json: str | None = None
    output_text: str | None = None
    created_at: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
