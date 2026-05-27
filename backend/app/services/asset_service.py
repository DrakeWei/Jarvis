from __future__ import annotations

from datetime import datetime, timezone
import json
import re

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.core import session_assets as session_asset_utils
from app.db.session import create_session
from app.models import AssetChunkRecord, MessageAssetRecord, SessionAssetRecord
from app.schemas.assets import SessionAssetChunkSummary, SessionAssetSummary


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _load_metadata(value: str | None) -> dict[str, object]:
    raw = str(value or "").strip()
    if not raw:
        return {}
    try:
        loaded = json.loads(raw)
    except Exception:
        return {}
    return loaded if isinstance(loaded, dict) else {}


def _dump_metadata(value: dict[str, object] | None) -> str | None:
    if not value:
        return None
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


def _default_origin_for_kind(kind: str) -> str:
    normalized = str(kind or "").strip().lower()
    if normalized.startswith("generated_"):
        return "generated"
    if normalized.startswith("derived_"):
        return "derived"
    return "uploaded"


def _to_asset_summary(row: SessionAssetRecord) -> SessionAssetSummary:
    return SessionAssetSummary(
        id=row.id,
        session_id=row.session_id,
        kind=row.kind,
        origin=row.origin,
        source_asset_id=row.source_asset_id,
        mime_type=row.mime_type,
        filename=row.filename,
        size_bytes=row.size_bytes,
        sha256=row.sha256,
        storage_path=row.storage_path,
        preview_path=row.preview_path,
        metadata_json=_load_metadata(row.metadata_json),
        status=row.status,
        error_message=row.error_message,
        created_at=row.created_at.isoformat(),
        updated_at=row.updated_at.isoformat(),
    )


def _to_chunk_summary(row: AssetChunkRecord) -> SessionAssetChunkSummary:
    return SessionAssetChunkSummary(
        id=row.id,
        asset_id=row.asset_id,
        chunk_index=row.chunk_index,
        page_number=row.page_number,
        sheet_name=row.sheet_name,
        slide_number=row.slide_number,
        section_path=row.section_path,
        start_ms=row.start_ms,
        end_ms=row.end_ms,
        speaker=row.speaker,
        frame_index=row.frame_index,
        frame_timestamp_ms=row.frame_timestamp_ms,
        content=row.content,
        summary=row.summary,
        char_count=row.char_count,
        created_at=row.created_at.isoformat(),
    )


def build_asset_reference(asset: SessionAssetSummary) -> dict[str, object]:
    return {
        "type": "asset_ref",
        "asset_id": asset.id,
        "filename": asset.filename,
        "kind": asset.kind,
        "origin": getattr(asset, "origin", "generated"),
        "source_asset_id": getattr(asset, "source_asset_id", None),
        "metadata_json": getattr(asset, "metadata_json", None),
        "status": getattr(asset, "status", "ready"),
        "preview_path": getattr(asset, "preview_path", None),
        "storage_path": getattr(asset, "storage_path", None),
    }


def create_asset_record(
    session_id: str,
    *,
    asset_id: str | None = None,
    kind: str,
    mime_type: str,
    filename: str,
    origin: str | None = None,
    source_asset_id: str | None = None,
    metadata_json: dict[str, object] | None = None,
    size_bytes: int = 0,
    sha256: str = "",
    status: str = "uploaded",
    preview_path: str | None = None,
    error_message: str | None = None,
    storage_path: str | None = None,
) -> SessionAssetSummary:
    resolved_asset_id = asset_id or session_asset_utils.new_asset_id()
    resolved_storage_path = storage_path or session_asset_utils.allocate_original_path(session_id, resolved_asset_id, filename).as_posix()
    with create_session() as db:
        row = SessionAssetRecord(
            id=resolved_asset_id,
            session_id=session_id,
            kind=kind,
            origin=(origin or _default_origin_for_kind(kind)).strip() or _default_origin_for_kind(kind),
            source_asset_id=str(source_asset_id).strip() if source_asset_id else None,
            metadata_json=_dump_metadata(metadata_json),
            mime_type=mime_type,
            filename=session_asset_utils.display_filename(filename),
            size_bytes=max(0, int(size_bytes)),
            sha256=sha256,
            storage_path=resolved_storage_path,
            preview_path=preview_path,
            status=status,
            error_message=error_message,
        )
        db.add(row)
        db.commit()
        db.refresh(row)
        return _to_asset_summary(row)


def list_assets(session_id: str) -> list[SessionAssetSummary]:
    with create_session() as db:
        rows = db.scalars(
            select(SessionAssetRecord)
            .where(SessionAssetRecord.session_id == session_id, SessionAssetRecord.hidden.is_(False))
            .order_by(SessionAssetRecord.created_at.desc(), SessionAssetRecord.id.desc())
        ).all()
        return [_to_asset_summary(row) for row in rows]


def get_asset(asset_id: str, *, session_id: str | None = None) -> SessionAssetSummary | None:
    with create_session() as db:
        stmt = select(SessionAssetRecord).where(
            SessionAssetRecord.id == asset_id,
            SessionAssetRecord.hidden.is_(False),
        )
        if session_id:
            stmt = stmt.where(SessionAssetRecord.session_id == session_id)
        row = db.scalars(stmt.limit(1)).first()
        return _to_asset_summary(row) if row else None


def update_asset_record(
    asset_id: str,
    *,
    status: str | None = None,
    preview_path: str | None = None,
    error_message: str | None = None,
    storage_path: str | None = None,
    sha256: str | None = None,
    origin: str | None = None,
    source_asset_id: str | None = None,
    metadata_json: dict[str, object] | None = None,
) -> SessionAssetSummary | None:
    with create_session() as db:
        row = db.get(SessionAssetRecord, asset_id)
        if row is None or row.hidden:
            return None
        if status is not None:
            row.status = status
        if preview_path is not None:
            row.preview_path = preview_path
        if error_message is not None:
            row.error_message = error_message
        if storage_path is not None:
            row.storage_path = storage_path
        if sha256 is not None:
            row.sha256 = sha256
        if origin is not None:
            row.origin = origin.strip() or row.origin
        if source_asset_id is not None:
            row.source_asset_id = source_asset_id.strip() or None
        if metadata_json is not None:
            row.metadata_json = _dump_metadata(metadata_json)
        row.updated_at = _utcnow()
        db.commit()
        db.refresh(row)
        return _to_asset_summary(row)


def hide_asset(asset_id: str, *, session_id: str | None = None) -> bool:
    with create_session() as db:
        row = db.get(SessionAssetRecord, asset_id)
        if row is None or row.hidden:
            return False
        if session_id is not None and row.session_id != session_id:
            return False
        row.hidden = True
        row.updated_at = _utcnow()
        db.commit()
        return True


def link_message_assets(
    message_id: int,
    session_id: str,
    asset_ids: list[str],
    *,
    db: Session | None = None,
) -> list[str]:
    normalized_ids = [asset_id.strip() for asset_id in asset_ids if asset_id and asset_id.strip()]
    if not normalized_ids:
        return []
    if db is not None:
        rows = db.scalars(
            select(SessionAssetRecord).where(
                SessionAssetRecord.id.in_(normalized_ids),
                SessionAssetRecord.session_id == session_id,
                SessionAssetRecord.hidden.is_(False),
            )
        ).all()
        found_by_id = {row.id: row for row in rows}
        missing = [asset_id for asset_id in normalized_ids if asset_id not in found_by_id]
        if missing:
            missing_text = ", ".join(missing)
            raise ValueError(f"Unknown or unavailable session asset(s): {missing_text}")
        for asset_id in normalized_ids:
            db.add(MessageAssetRecord(message_id=message_id, asset_id=asset_id))
        return normalized_ids
    with create_session() as db:
        linked = link_message_assets(message_id, session_id, normalized_ids, db=db)
        db.commit()
        return linked


def list_message_asset_ids(message_id: int) -> list[str]:
    with create_session() as db:
        return list(
            db.scalars(
                select(MessageAssetRecord.asset_id)
                .where(MessageAssetRecord.message_id == message_id)
                .order_by(MessageAssetRecord.id.asc())
            ).all()
        )


def create_asset_chunk(
    asset_id: str,
    *,
    chunk_index: int,
    content: str,
    page_number: int | None = None,
    sheet_name: str | None = None,
    slide_number: int | None = None,
    section_path: str | None = None,
    start_ms: int | None = None,
    end_ms: int | None = None,
    speaker: str | None = None,
    frame_index: int | None = None,
    frame_timestamp_ms: int | None = None,
    summary: str | None = None,
) -> SessionAssetChunkSummary:
    with create_session() as db:
        row = AssetChunkRecord(
            asset_id=asset_id,
            chunk_index=chunk_index,
            page_number=page_number,
            sheet_name=sheet_name,
            slide_number=slide_number,
            section_path=section_path,
            start_ms=start_ms,
            end_ms=end_ms,
            speaker=speaker,
            frame_index=frame_index,
            frame_timestamp_ms=frame_timestamp_ms,
            content=content,
            summary=summary,
            char_count=len(content),
        )
        db.add(row)
        db.commit()
        db.refresh(row)
        return _to_chunk_summary(row)


def list_asset_chunks(asset_id: str) -> list[SessionAssetChunkSummary]:
    with create_session() as db:
        rows = db.scalars(
            select(AssetChunkRecord)
            .where(AssetChunkRecord.asset_id == asset_id)
            .order_by(AssetChunkRecord.chunk_index.asc(), AssetChunkRecord.id.asc())
        ).all()
        return [_to_chunk_summary(row) for row in rows]


def delete_asset_chunks(asset_id: str) -> int:
    with create_session() as db:
        result = db.execute(delete(AssetChunkRecord).where(AssetChunkRecord.asset_id == asset_id))
        db.commit()
        return int(result.rowcount or 0)


def search_asset_chunks(asset_id: str, query_text: str, *, limit: int = 3) -> list[SessionAssetChunkSummary]:
    chunks = list_asset_chunks(asset_id)
    if not chunks:
        return []
    query_tokens = {
        token
        for token in re.split(r"[^A-Za-z0-9_\-./]+", query_text.lower())
        if len(token) >= 2
    }
    if not query_tokens:
        return chunks[: max(1, limit)]

    ranked: list[tuple[int, SessionAssetChunkSummary]] = []
    for chunk in chunks:
        haystack = f"{chunk.content}\n{chunk.summary or ''}".lower()
        score = sum(1 for token in query_tokens if token in haystack)
        if score <= 0:
            continue
        ranked.append((score, chunk))
    if not ranked:
        return chunks[: max(1, limit)]
    ranked.sort(key=lambda item: (-item[0], item[1].chunk_index, item[1].id))
    return [chunk for _, chunk in ranked[: max(1, limit)]]


def get_asset_chunk_by_index(asset_id: str, chunk_index: int) -> SessionAssetChunkSummary | None:
    with create_session() as db:
        row = db.scalars(
            select(AssetChunkRecord)
            .where(AssetChunkRecord.asset_id == asset_id, AssetChunkRecord.chunk_index == chunk_index)
            .order_by(AssetChunkRecord.id.asc())
            .limit(1)
        ).first()
        return _to_chunk_summary(row) if row else None
