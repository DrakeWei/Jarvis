from pydantic import BaseModel, Field


class RuntimeObservabilitySummary(BaseModel):
    runtime_role: str
    instance_id: str
    configured_event_bus_backend: str
    effective_event_bus_backend: str
    dispatcher_running: bool
    total_sessions: int
    total_ws_subscribers: int
    ephemeral_events_dropped: int
    scheduled_background_jobs: int
    scheduled_ingestion_jobs: int
    background_jobs_by_status: dict[str, int] = Field(default_factory=dict)
    ingestion_jobs_by_status: dict[str, int] = Field(default_factory=dict)
    retrying_turn_jobs: int = 0
    retrying_ingestion_jobs: int = 0
    oldest_queued_turn_job_age_seconds: float | None = None
    oldest_queued_ingestion_job_age_seconds: float | None = None
    oldest_running_turn_job_age_seconds: float | None = None
    oldest_running_ingestion_job_age_seconds: float | None = None
    oldest_running_turn_age_seconds: float | None = None
    turns_by_status: dict[str, int] = Field(default_factory=dict)
