from __future__ import annotations

import re
from typing import Any

from feishu_mcp_server.config import settings
from feishu_mcp_server.doc_parser import linearize_blocks
from feishu_mcp_server.feishu_client import feishu_client


class FeishuDocServiceError(RuntimeError):
    pass


def create_doc(arguments: dict[str, Any]) -> dict[str, Any]:
    title = str(arguments.get("title") or "").strip()
    if not title:
        raise FeishuDocServiceError("title is required.")
    folder_token = str(arguments.get("folder_token") or "").strip()
    payload = feishu_client.create_doc(title=title, folder_token=folder_token)
    document = _unwrap_document(payload)
    document_id = _extract_document_id(document)
    return {
        "document_id": document_id,
        "title": _extract_title(document) or title,
        "revision_id": _extract_revision(document),
        "url": _doc_url(document_id),
        "note": "initial_blocks is not implemented yet." if arguments.get("initial_blocks") else "",
    }


def get_doc(arguments: dict[str, Any]) -> dict[str, Any]:
    document_id = resolve_document_id(arguments)
    payload = feishu_client.get_doc(document_id)
    document = _unwrap_document(payload)
    return {
        "document_id": document_id,
        "title": _extract_title(document),
        "revision_id": _extract_revision(document),
        "url": _doc_url(document_id),
        "raw": document,
    }


def read_doc(arguments: dict[str, Any]) -> dict[str, Any]:
    document_id = resolve_document_id(arguments)
    page_token = ""
    all_blocks: list[dict[str, Any]] = []
    max_blocks = int(arguments.get("max_blocks") or 0)

    while True:
        payload = feishu_client.list_doc_blocks(document_id, page_token=page_token)
        items = payload.get("data", {}).get("items", [])
        if isinstance(items, list):
            for item in items:
                if isinstance(item, dict):
                    all_blocks.append(item)
                    if max_blocks and len(all_blocks) >= max_blocks:
                        break
        if max_blocks and len(all_blocks) >= max_blocks:
            break
        page_token = str(payload.get("data", {}).get("page_token") or "")
        has_more = bool(payload.get("data", {}).get("has_more", False))
        if not has_more or not page_token:
            break

    doc_meta = get_doc(arguments)
    parsed = linearize_blocks(all_blocks[: max_blocks or None])
    return {
        "document_id": document_id,
        "title": doc_meta.get("title", ""),
        "revision_id": doc_meta.get("revision_id"),
        "url": doc_meta.get("url", ""),
        **parsed,
        "truncated": bool(max_blocks and len(all_blocks) > max_blocks),
    }


def not_implemented(name: str) -> dict[str, Any]:
    raise FeishuDocServiceError(f"{name} is not implemented yet.")


def resolve_document_id(arguments: dict[str, Any]) -> str:
    document_id = str(arguments.get("document_id") or "").strip()
    if document_id:
        return document_id
    document_url = str(arguments.get("document_url") or "").strip()
    if document_url:
        match = re.search(r"/docx/([A-Za-z0-9]+)", document_url)
        if match:
            return match.group(1)
    raise FeishuDocServiceError("document_id or document_url is required.")


def _unwrap_document(payload: dict[str, Any]) -> dict[str, Any]:
    data = payload.get("data", {})
    if not isinstance(data, dict):
        raise FeishuDocServiceError("Feishu returned malformed document data.")
    document = data.get("document", data)
    if not isinstance(document, dict):
        raise FeishuDocServiceError("Feishu returned malformed document payload.")
    return document


def _extract_document_id(document: dict[str, Any]) -> str:
    document_id = str(document.get("document_id") or document.get("documentId") or "").strip()
    if not document_id:
        raise FeishuDocServiceError("Feishu returned no document_id.")
    return document_id


def _extract_title(document: dict[str, Any]) -> str:
    return str(document.get("title") or "").strip()


def _extract_revision(document: dict[str, Any]) -> int | None:
    revision = document.get("revision_id", document.get("revisionId"))
    try:
        return int(revision) if revision is not None else None
    except (TypeError, ValueError):
        return None


def _doc_url(document_id: str) -> str:
    return f"{settings.doc_base_url}/{document_id}"
