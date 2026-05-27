from pydantic import BaseModel


class TurnSummary(BaseModel):
    id: int
    session_id: str
    task_id: int | None = None
    branch_context_id: str | None = None
    user_message_id: int | None
    workspace_path: str | None
    workspace_fingerprint: str | None
    execution_mode: str = "normal"
    status: str
    started_at: str
    updated_at: str
    completed_at: str | None
    cancel_requested: bool = False
    last_checkpoint_seq: int
    resume_hint: str | None
    error_summary: str | None
    resumable: bool = False
