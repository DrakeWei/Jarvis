from datetime import datetime, timezone

from pydantic import BaseModel, Field


class SessionAssetReference(BaseModel):
    asset_id: str


class SessionAssetSummary(BaseModel):
    id: str
    session_id: str
    kind: str
    origin: str = "uploaded"
    source_asset_id: str | None = None
    mime_type: str
    filename: str
    size_bytes: int
    sha256: str
    storage_path: str
    preview_path: str | None
    metadata_json: dict[str, object] = Field(default_factory=dict)
    status: str
    error_message: str | None
    created_at: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    updated_at: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())


class SessionAssetChunkSummary(BaseModel):
    id: int
    asset_id: str
    chunk_index: int
    page_number: int | None
    sheet_name: str | None
    slide_number: int | None
    section_path: str | None
    start_ms: int | None = None
    end_ms: int | None = None
    speaker: str | None = None
    frame_index: int | None = None
    frame_timestamp_ms: int | None = None
    content: str
    summary: str | None
    char_count: int
    created_at: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())


class IngestionJobSummary(BaseModel):
    id: int
    session_id: str
    asset_id: str
    job_type: str
    status: str
    attempts: int
    owner_id: str | None
    last_error: str | None
    created_at: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    updated_at: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    started_at: str | None = None
    completed_at: str | None = None
