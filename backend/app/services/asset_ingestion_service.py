from __future__ import annotations

import hashlib
import mimetypes
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from docx import Document as DocxDocument
from openpyxl import load_workbook
from PIL import Image
from pypdf import PdfReader
from pptx import Presentation

from app.core.config import settings
from app.core import session_assets as session_asset_utils
from app.schemas.assets import SessionAssetSummary
from app.services import asset_service


class AssetUploadError(ValueError):
    pass


class AsyncUploadLike(Protocol):
    filename: str | None
    content_type: str | None

    async def read(self, size: int = -1) -> bytes: ...


@dataclass
class ParsedChunk:
    content: str
    page_number: int | None = None
    sheet_name: str | None = None
    slide_number: int | None = None
    section_path: str | None = None
    summary: str | None = None


def detect_asset_kind(filename: str, mime_type: str) -> str:
    suffix = Path(filename).suffix.lower()
    lowered_mime = (mime_type or "").lower()
    if lowered_mime.startswith("image/") or suffix in {".png", ".jpg", ".jpeg", ".webp", ".gif", ".bmp"}:
        return "image"
    if lowered_mime == "application/pdf" or suffix == ".pdf":
        return "pdf"
    if suffix == ".docx":
        return "docx"
    if suffix == ".xlsx":
        return "xlsx"
    if suffix == ".pptx":
        return "pptx"
    return "other"


def normalize_mime_type(filename: str, mime_type: str | None) -> str:
    if mime_type and mime_type.strip():
        return mime_type.strip().lower()
    guessed, _ = mimetypes.guess_type(filename)
    return (guessed or "application/octet-stream").lower()


def validate_upload(filename: str, mime_type: str, size_bytes: int) -> str:
    if not filename.strip():
        raise AssetUploadError("Uploaded file is missing a filename.")
    if size_bytes <= 0:
        raise AssetUploadError("Uploaded file is empty.")
    if size_bytes > settings.jarvis_asset_max_file_bytes:
        raise AssetUploadError("Uploaded file exceeds the maximum allowed size.")
    kind = detect_asset_kind(filename, mime_type)
    if kind == "image" and size_bytes > settings.jarvis_asset_max_image_bytes:
        raise AssetUploadError("Uploaded image exceeds the maximum allowed size.")
    if kind == "other":
        raise AssetUploadError("Unsupported file type. Jarvis currently accepts images, PDF, DOCX, XLSX, and PPTX.")
    return kind


def stage_uploaded_asset(session_id: str, filename: str, mime_type: str | None, data: bytes) -> SessionAssetSummary:
    normalized_mime = normalize_mime_type(filename, mime_type)
    kind = validate_upload(filename, normalized_mime, len(data))
    sha256 = hashlib.sha256(data).hexdigest()
    asset = asset_service.create_asset_record(
        session_id,
        kind=kind,
        mime_type=normalized_mime,
        filename=filename,
        size_bytes=len(data),
        sha256=sha256,
        status="uploaded",
    )
    session_asset_utils.ensure_asset_dirs(session_id, asset.id)
    original_path = Path(asset.storage_path)
    original_path.write_bytes(data)
    updated = asset_service.update_asset_record(asset.id, storage_path=original_path.as_posix(), sha256=sha256)
    return updated or asset


async def stage_uploaded_asset_stream(
    session_id: str,
    upload: AsyncUploadLike,
    *,
    chunk_size: int = 1024 * 1024,
) -> SessionAssetSummary:
    filename = upload.filename or ""
    normalized_mime = normalize_mime_type(filename, upload.content_type)
    kind = detect_asset_kind(filename, normalized_mime)
    if kind == "other":
        raise AssetUploadError("Unsupported file type. Jarvis currently accepts images, PDF, DOCX, XLSX, and PPTX.")

    asset_id = session_asset_utils.new_asset_id()
    session_asset_utils.ensure_asset_dirs(session_id, asset_id)
    original_path = session_asset_utils.allocate_original_path(session_id, asset_id, filename)

    sha256_hasher = hashlib.sha256()
    size_bytes = 0
    try:
        with original_path.open("wb") as handle:
            while True:
                chunk = await upload.read(chunk_size)
                if not chunk:
                    break
                handle.write(chunk)
                sha256_hasher.update(chunk)
                size_bytes += len(chunk)

        validate_upload(filename, normalized_mime, size_bytes)
        asset = asset_service.create_asset_record(
            session_id,
            asset_id=asset_id,
            kind=kind,
            mime_type=normalized_mime,
            filename=filename,
            size_bytes=size_bytes,
            sha256=sha256_hasher.hexdigest(),
            status="uploaded",
            storage_path=original_path.as_posix(),
        )
        return asset
    except Exception:
        try:
            original_path.unlink(missing_ok=True)
        except Exception:
            pass
        raise


def ingest_asset(asset_id: str) -> SessionAssetSummary:
    asset = asset_service.get_asset(asset_id)
    if asset is None:
        raise AssetUploadError(f"Unknown asset {asset_id}")
    asset_service.delete_asset_chunks(asset.id)
    asset_service.update_asset_record(asset.id, status="processing", error_message="")
    refreshed = asset_service.get_asset(asset.id)
    if refreshed is None:
        raise AssetUploadError(f"Unknown asset {asset_id}")

    try:
        if refreshed.kind == "image":
            preview_path = _ingest_image(refreshed)
            return asset_service.update_asset_record(
                refreshed.id,
                status="ready",
                preview_path=preview_path,
                error_message="",
            ) or refreshed

        chunks = _extract_document_chunks(refreshed)
        extracted_text = "\n\n".join(chunk.content for chunk in chunks).strip()
        text_path = session_asset_utils.allocate_extracted_text_path(refreshed.session_id, refreshed.id)
        text_path.write_text(extracted_text)
        for index, chunk in enumerate(chunks):
            asset_service.create_asset_chunk(
                refreshed.id,
                chunk_index=index,
                content=chunk.content,
                page_number=chunk.page_number,
                sheet_name=chunk.sheet_name,
                slide_number=chunk.slide_number,
                section_path=chunk.section_path,
                summary=chunk.summary,
            )
        return asset_service.update_asset_record(
            refreshed.id,
            status="ready",
            error_message="",
        ) or refreshed
    except Exception as exc:
        failed = asset_service.update_asset_record(
            refreshed.id,
            status="failed",
            error_message=str(exc),
        )
        if failed is None:
            raise
        return failed


def _ingest_image(asset: SessionAssetSummary) -> str:
    original_path = Path(asset.storage_path)
    preview_path = session_asset_utils.allocate_preview_path(asset.session_id, asset.id)
    with Image.open(original_path) as image:
        preview = image.convert("RGB")
        preview.thumbnail((320, 320))
        preview.save(preview_path, format="PNG")
    return preview_path.as_posix()


def _extract_document_chunks(asset: SessionAssetSummary) -> list[ParsedChunk]:
    original_path = Path(asset.storage_path)
    if asset.kind == "pdf":
        return _extract_pdf_chunks(original_path)
    if asset.kind == "docx":
        return _extract_docx_chunks(original_path)
    if asset.kind == "xlsx":
        return _extract_xlsx_chunks(original_path)
    if asset.kind == "pptx":
        return _extract_pptx_chunks(original_path)
    raise AssetUploadError(f"Unsupported asset kind for ingestion: {asset.kind}")


def _chunk_text_blocks(
    blocks: list[tuple[str, dict[str, object]]],
    *,
    char_limit: int,
) -> list[ParsedChunk]:
    chunks: list[ParsedChunk] = []
    buffer: list[str] = []
    current_meta: dict[str, object] = {}

    def flush() -> None:
        if not buffer:
            return
        content = "\n".join(buffer).strip()
        if not content:
            buffer.clear()
            return
        chunks.append(
            ParsedChunk(
                content=content,
                page_number=_as_optional_int(current_meta.get("page_number")),
                sheet_name=_as_optional_str(current_meta.get("sheet_name")),
                slide_number=_as_optional_int(current_meta.get("slide_number")),
                section_path=_as_optional_str(current_meta.get("section_path")),
                summary=content[:200],
            )
        )
        buffer.clear()

    for text, meta in blocks:
        cleaned = text.strip()
        if not cleaned:
            continue
        meta_changed = buffer and meta != current_meta
        projected = len("\n".join(buffer + [cleaned]))
        if meta_changed or projected > char_limit:
            flush()
        if not buffer:
            current_meta = dict(meta)
        buffer.append(cleaned)
    flush()
    return chunks


def _as_optional_int(value: object) -> int | None:
    return value if isinstance(value, int) else None


def _as_optional_str(value: object) -> str | None:
    return value if isinstance(value, str) and value.strip() else None


def _extract_pdf_chunks(path: Path) -> list[ParsedChunk]:
    reader = PdfReader(path)
    blocks: list[tuple[str, dict[str, object]]] = []
    for index, page in enumerate(reader.pages, start=1):
        text = page.extract_text() or ""
        blocks.append((text, {"page_number": index}))
    return _chunk_text_blocks(blocks, char_limit=settings.jarvis_asset_chunk_char_limit)


def _extract_docx_chunks(path: Path) -> list[ParsedChunk]:
    document = DocxDocument(path)
    blocks: list[tuple[str, dict[str, object]]] = []
    current_heading = ""
    for paragraph in document.paragraphs:
        text = paragraph.text.strip()
        if not text:
            continue
        style_name = getattr(getattr(paragraph, "style", None), "name", "") or ""
        if style_name.lower().startswith("heading"):
            current_heading = text
        blocks.append((text, {"section_path": current_heading or None}))
    for table_index, table in enumerate(document.tables, start=1):
        for row_index, row in enumerate(table.rows, start=1):
            values = [cell.text.strip() for cell in row.cells if cell.text and cell.text.strip()]
            if not values:
                continue
            blocks.append(
                ("\t".join(values), {"section_path": current_heading or f"table-{table_index}", "page_number": row_index})
            )
    return _chunk_text_blocks(blocks, char_limit=settings.jarvis_asset_chunk_char_limit)


def _extract_xlsx_chunks(path: Path) -> list[ParsedChunk]:
    workbook = load_workbook(path, read_only=True, data_only=True)
    blocks: list[tuple[str, dict[str, object]]] = []
    try:
        for sheet in workbook.worksheets:
            for row in sheet.iter_rows(values_only=True):
                values = [str(cell).strip() for cell in row if cell is not None and str(cell).strip()]
                if not values:
                    continue
                blocks.append(("\t".join(values), {"sheet_name": sheet.title}))
        return _chunk_text_blocks(blocks, char_limit=settings.jarvis_asset_chunk_char_limit)
    finally:
        workbook.close()


def _extract_pptx_chunks(path: Path) -> list[ParsedChunk]:
    presentation = Presentation(path)
    blocks: list[tuple[str, dict[str, object]]] = []
    for index, slide in enumerate(presentation.slides, start=1):
        texts: list[str] = []
        for shape in slide.shapes:
            text = getattr(shape, "text", "")
            if text and text.strip():
                texts.append(text.strip())
        notes_text = ""
        try:
            notes_frame = slide.notes_slide.notes_text_frame
            notes_text = notes_frame.text.strip() if notes_frame and notes_frame.text else ""
        except Exception:
            notes_text = ""
        if notes_text:
            texts.append(f"Notes: {notes_text}")
        if texts:
            blocks.append(("\n".join(texts), {"slide_number": index, "section_path": f"slide-{index}"}))
    return _chunk_text_blocks(blocks, char_limit=settings.jarvis_asset_chunk_char_limit)
