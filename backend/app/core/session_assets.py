from __future__ import annotations

import re
from pathlib import Path
from uuid import uuid4

from app.core.config import settings


_FILENAME_SANITIZER = re.compile(r"[^\w.\-]+", re.UNICODE)


def new_asset_id() -> str:
    return str(uuid4())


def session_assets_root(session_id: str) -> Path:
    return settings.data_dir / "sessions" / session_id / "assets"


def asset_root(session_id: str, asset_id: str) -> Path:
    return session_assets_root(session_id) / asset_id


def asset_original_dir(session_id: str, asset_id: str) -> Path:
    return asset_root(session_id, asset_id) / "original"


def asset_derived_dir(session_id: str, asset_id: str) -> Path:
    return asset_root(session_id, asset_id) / "derived"


def sanitize_filename(filename: str) -> str:
    raw = Path(filename or "").name.strip()
    if not raw:
        return "attachment"
    sanitized = _FILENAME_SANITIZER.sub("_", raw).strip("._")
    return sanitized or "attachment"


def display_filename(filename: str) -> str:
    raw = Path(filename or "").name.strip()
    return raw or "attachment"


def allocate_original_path(session_id: str, asset_id: str, filename: str) -> Path:
    return asset_original_dir(session_id, asset_id) / sanitize_filename(filename)


def allocate_preview_path(session_id: str, asset_id: str, suffix: str = ".png") -> Path:
    extension = suffix if suffix.startswith(".") else f".{suffix}"
    return asset_derived_dir(session_id, asset_id) / f"preview{extension}"


def allocate_extracted_text_path(session_id: str, asset_id: str, filename: str = "extracted.txt") -> Path:
    return asset_derived_dir(session_id, asset_id) / sanitize_filename(filename)


def ensure_asset_dirs(session_id: str, asset_id: str) -> tuple[Path, Path]:
    original_dir = asset_original_dir(session_id, asset_id)
    derived_dir = asset_derived_dir(session_id, asset_id)
    original_dir.mkdir(parents=True, exist_ok=True)
    derived_dir.mkdir(parents=True, exist_ok=True)
    return original_dir, derived_dir
