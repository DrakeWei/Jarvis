from datetime import datetime, timezone

from pydantic import BaseModel, Field


class SessionAssetReference(BaseModel):
    asset_id: str


class SessionAssetSummary(BaseModel):
    id: str
    session_id: str
    kind: str
    mime_type: str
    filename: str
    size_bytes: int
    sha256: str
    storage_path: str
    preview_path: str | None
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
    content: str
    summary: str | None
    char_count: int
    created_at: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
