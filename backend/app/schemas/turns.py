from pydantic import BaseModel


class TurnSummary(BaseModel):
    id: int
    session_id: str
    user_message_id: int | None
    workspace_path: str | None
    workspace_fingerprint: str | None
    status: str
    started_at: str
    updated_at: str
    completed_at: str | None
    cancel_requested: bool = False
    last_checkpoint_seq: int
    resume_hint: str | None
    error_summary: str | None
    resumable: bool = False
