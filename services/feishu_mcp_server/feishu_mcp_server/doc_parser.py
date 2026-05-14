from __future__ import annotations

from typing import Any

NUMERIC_BLOCK_TYPES = {
    "1": "page",
    "2": "text",
    "3": "heading1",
    "4": "heading2",
    "5": "heading3",
    "6": "heading4",
    "7": "heading5",
    "8": "heading6",
    "9": "heading7",
    "10": "heading8",
    "11": "heading9",
    "12": "bullet",
    "13": "ordered",
    "14": "code",
    "15": "quote",
    "16": "todo",
}


def linearize_blocks(block_items: list[dict[str, Any]]) -> dict[str, Any]:
    headings: list[dict[str, Any]] = []
    blocks: list[dict[str, Any]] = []
    plain_parts: list[str] = []

    for item in block_items:
        block_id = str(item.get("block_id") or item.get("blockId") or "")
        parent_id = str(item.get("parent_id") or item.get("parentId") or "")
        children = item.get("children", [])
        child_count = len(children) if isinstance(children, list) else 0
        block_type = _block_type(item)
        text = _extract_text(item).strip()
        entry = {
            "block_id": block_id,
            "parent_id": parent_id,
            "block_type": block_type,
            "text": text,
            "child_count": child_count,
        }
        blocks.append(entry)
        if text:
            plain_parts.append(text)
        if block_type.startswith("heading"):
            headings.append(entry)

    return {
        "plain_text": "\n".join(part for part in plain_parts if part).strip(),
        "headings": headings,
        "blocks": blocks,
    }


def _block_type(block: dict[str, Any]) -> str:
    raw = block.get("block_type") or block.get("blockType") or block.get("type") or "unknown"
    text = str(raw)
    return NUMERIC_BLOCK_TYPES.get(text, text)


def _extract_text(block: dict[str, Any]) -> str:
    texts: list[str] = []
    _collect_text(block, texts)
    return " ".join(part.strip() for part in texts if part and part.strip())


def _collect_text(value: Any, texts: list[str]) -> None:
    if isinstance(value, str):
        texts.append(value)
        return
    if isinstance(value, dict):
        for key, item in value.items():
            if key in {"block_id", "blockId", "parent_id", "children"}:
                continue
            _collect_text(item, texts)
        return
    if isinstance(value, list):
        for item in value:
            _collect_text(item, texts)
