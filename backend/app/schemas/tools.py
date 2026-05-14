from datetime import datetime, timezone

from pydantic import BaseModel, Field


class ToolExecutionSummary(BaseModel):
    id: int
    session_id: str
    tool_name: str
    tool_source: str = "local"
    server_name: str | None = None
    status: str
    input_json: str | None = None
    output_text: str | None = None
    latency_ms: int | None = None
    remote_request_id: str | None = None
    created_at: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
