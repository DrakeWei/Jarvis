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
    result = {
        "document_id": document_id,
        "title": _extract_title(document) or title,
        "revision_id": _extract_revision(document),
        "url": _doc_url(document_id),
    }
    share_targets = _share_targets(arguments)
    if share_targets:
        try:
            share_payload = feishu_client.add_permission_members(token=document_id, members=share_targets, file_type="docx")
            result["share_result"] = {
                "requested_members": share_targets,
                "raw": share_payload.get("data", share_payload),
            }
        except Exception as exc:
            result["share_error"] = str(exc)
    initial_blocks = arguments.get("initial_blocks")
    if initial_blocks:
        append_result = append_doc(
            {
                "document_id": document_id,
                "blocks": initial_blocks,
            }
        )
        result["seed_result"] = append_result
    return result


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
    raw_blocks = list_all_blocks(document_id, max_blocks=int(arguments.get("max_blocks") or 0))
    doc_meta = get_doc(arguments)
    parsed = linearize_blocks(raw_blocks)
    return {
        "document_id": document_id,
        "title": doc_meta.get("title", ""),
        "revision_id": doc_meta.get("revision_id"),
        "url": doc_meta.get("url", ""),
        **parsed,
        "truncated": bool(arguments.get("max_blocks") and len(raw_blocks) >= int(arguments["max_blocks"])),
    }


def append_doc(arguments: dict[str, Any]) -> dict[str, Any]:
    document_id = resolve_document_id(arguments)
    markdown = _resolve_markdown(arguments)
    converted = feishu_client.convert_markdown_to_blocks(markdown)
    converted_data = _unwrap_data(converted)
    children_id, descendants = _converted_descendants(converted_data)
    payload = feishu_client.create_nested_blocks(
        document_id=document_id,
        block_id=document_id,
        children_id=children_id,
        descendants=descendants,
        index=-1,
    )
    return {
        "document_id": document_id,
        "url": _doc_url(document_id),
        "inserted_children_count": len(children_id),
        "revision_id": _extract_revision_from_data(payload),
        "markdown": markdown,
    }


def insert_after_heading(arguments: dict[str, Any]) -> dict[str, Any]:
    document_id = resolve_document_id(arguments)
    heading_query = str(arguments.get("heading_query") or "").strip()
    if not heading_query:
        raise FeishuDocServiceError("heading_query is required.")
    raw_blocks = list_all_blocks(document_id)
    parsed = linearize_blocks(raw_blocks)
    heading = _match_heading(parsed.get("blocks", []), heading_query, document_id=document_id)
    if heading is None:
        raise FeishuDocServiceError(f"No heading matched '{heading_query}'.")
    if heading["parent_id"] != document_id:
        raise FeishuDocServiceError("Only top-level heading insertion is implemented in this phase.")

    top_level = [block for block in parsed.get("blocks", []) if block.get("parent_id") == document_id]
    heading_index = _index_of_block(top_level, str(heading["block_id"]))
    if heading_index < 0:
        raise FeishuDocServiceError("Unable to determine insertion index for the matched heading.")

    markdown = _resolve_markdown(arguments)
    converted = feishu_client.convert_markdown_to_blocks(markdown)
    converted_data = _unwrap_data(converted)
    children_id, descendants = _converted_descendants(converted_data)
    payload = feishu_client.create_nested_blocks(
        document_id=document_id,
        block_id=document_id,
        children_id=children_id,
        descendants=descendants,
        index=heading_index + 1,
    )
    return {
        "document_id": document_id,
        "matched_heading": heading,
        "inserted_children_count": len(children_id),
        "revision_id": _extract_revision_from_data(payload),
        "markdown": markdown,
    }


def replace_text(arguments: dict[str, Any]) -> dict[str, Any]:
    document_id = resolve_document_id(arguments)
    find_text = str(arguments.get("find_text") or "").strip()
    replace_with = str(arguments.get("replace_text") or "")
    scope = str(arguments.get("scope") or "").strip()
    confirm = bool(arguments.get("confirm", False))
    if not find_text or not scope:
        raise FeishuDocServiceError("find_text and scope are required.")
    raw_blocks = list_all_blocks(document_id)
    parsed = linearize_blocks(raw_blocks)
    top_level = [block for block in parsed.get("blocks", []) if block.get("parent_id") == document_id]
    candidates = _matching_text_blocks(
        parsed.get("blocks", []),
        find_text,
        heading_query=str(arguments.get("heading_query") or "").strip(),
    )
    if not candidates:
        raise FeishuDocServiceError(f"No text match found for '{find_text}'.")
    if scope not in {"first", "all", "heading_scoped"}:
        raise FeishuDocServiceError("scope must be one of: first, all, heading_scoped.")
    selected = candidates[:1] if scope == "first" else candidates
    preview = [
        {
            "block_id": block["block_id"],
            "before": block["text"],
            "after": block["text"].replace(find_text, replace_with),
        }
        for block in selected
    ]
    if not confirm:
        return {
            "document_id": document_id,
            "match_count": len(selected),
            "needs_confirmation": True,
            "preview": preview,
        }

    for block in selected:
        if block.get("parent_id") != document_id:
            raise FeishuDocServiceError("replace_text execution currently supports top-level blocks only.")

    revisions: list[int | None] = []
    for block in reversed(selected):
        new_text = str(block["text"]).replace(find_text, replace_with)
        replacement_markdown = _markdown_for_existing_block(block, new_text)
        index = _index_of_block(top_level, str(block["block_id"]))
        if index < 0:
            raise FeishuDocServiceError("Unable to determine block index during replace_text execution.")
        delete_payload = feishu_client.delete_child_range(
            document_id=document_id,
            block_id=document_id,
            start_index=index,
            end_index=index + 1,
        )
        converted = feishu_client.convert_markdown_to_blocks(replacement_markdown)
        converted_data = _unwrap_data(converted)
        children_id, descendants = _converted_descendants(converted_data)
        create_payload = feishu_client.create_nested_blocks(
            document_id=document_id,
            block_id=document_id,
            children_id=children_id,
            descendants=descendants,
            index=index,
        )
        revisions.append(_extract_revision_from_data(create_payload) or _extract_revision_from_data(delete_payload))
    return {
        "document_id": document_id,
        "match_count": len(selected),
        "preview": preview,
        "executed": True,
        "revision_id": revisions[-1] if revisions else None,
    }


def delete_blocks(arguments: dict[str, Any]) -> dict[str, Any]:
    document_id = resolve_document_id(arguments)
    heading_query = str(arguments.get("heading_query") or "").strip()
    confirm = bool(arguments.get("confirm", False))
    if not heading_query:
        raise FeishuDocServiceError("heading_query is required in this phase.")
    raw_blocks = list_all_blocks(document_id)
    parsed = linearize_blocks(raw_blocks)
    heading = _match_heading(parsed.get("blocks", []), heading_query, document_id=document_id)
    if heading is None:
        raise FeishuDocServiceError(f"No heading matched '{heading_query}'.")
    top_level = [block for block in parsed.get("blocks", []) if block.get("parent_id") == document_id]
    heading_index = _index_of_block(top_level, str(heading["block_id"]))
    if heading_index < 0:
        raise FeishuDocServiceError("Unable to determine delete range for the matched heading.")

    next_heading_index = None
    for index in range(heading_index + 1, len(top_level)):
        block = top_level[index]
        if str(block.get("block_type", "")).startswith("heading"):
            next_heading_index = index
            break
    preview = top_level[heading_index:next_heading_index]
    delete_range = {
        "parent_block_id": document_id,
        "start_index": heading_index,
        "end_index": next_heading_index if next_heading_index is not None else len(top_level),
    }
    if not confirm:
        return {
            "document_id": document_id,
            "needs_confirmation": True,
            "matched_heading": heading,
            "delete_range": delete_range,
            "preview": preview,
        }

    payload = feishu_client.delete_child_range(
        document_id=document_id,
        block_id=document_id,
        start_index=delete_range["start_index"],
        end_index=delete_range["end_index"],
    )
    return {
        "document_id": document_id,
        "matched_heading": heading,
        "delete_range": delete_range,
        "preview": preview,
        "executed": True,
        "revision_id": _extract_revision_from_data(payload),
    }


def list_all_blocks(document_id: str, max_blocks: int = 0) -> list[dict[str, Any]]:
    page_token = ""
    all_blocks: list[dict[str, Any]] = []
    while True:
        payload = feishu_client.list_doc_blocks(document_id, page_token=page_token)
        items = payload.get("data", {}).get("items", [])
        if isinstance(items, list):
            for item in items:
                if isinstance(item, dict):
                    all_blocks.append(item)
                    if max_blocks and len(all_blocks) >= max_blocks:
                        return all_blocks[:max_blocks]
        page_token = str(payload.get("data", {}).get("page_token") or "")
        has_more = bool(payload.get("data", {}).get("has_more", False))
        if not has_more or not page_token:
            break
    return all_blocks


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


def render_markdown(blocks: list[Any]) -> str:
    lines: list[str] = []
    for block in blocks:
        if isinstance(block, str):
            lines.append(block.strip())
            continue
        if not isinstance(block, dict):
            lines.append(str(block))
            continue
        text = str(block.get("text") or block.get("content") or "").strip()
        kind = str(block.get("type") or block.get("kind") or "paragraph").strip().lower()
        if not text:
            continue
        if kind.startswith("heading"):
            level_suffix = kind.replace("heading", "").strip()
            level = int(level_suffix) if level_suffix.isdigit() else 1
            level = min(max(level, 1), 6)
            lines.append(f"{'#' * level} {text}")
        elif kind in {"bullet", "unordered", "list"}:
            lines.append(f"- {text}")
        elif kind in {"ordered", "numbered"}:
            lines.append(f"1. {text}")
        elif kind == "quote":
            lines.append(f"> {text}")
        elif kind == "code":
            lines.append(f"```\n{text}\n```")
        else:
            lines.append(text)
    markdown = "\n\n".join(line for line in lines if line).strip()
    if not markdown:
        raise FeishuDocServiceError("blocks produced no usable content.")
    return markdown


def _require_blocks(arguments: dict[str, Any]) -> list[Any]:
    blocks = arguments.get("blocks")
    if not isinstance(blocks, list) or not blocks:
        raise FeishuDocServiceError("blocks is required and must be a non-empty array.")
    return blocks


def _resolve_markdown(arguments: dict[str, Any]) -> str:
    content = arguments.get("content")
    if isinstance(content, str) and content.strip():
        return content.strip()
    blocks = arguments.get("blocks")
    if isinstance(blocks, list) and blocks:
        return render_markdown(blocks)
    raise FeishuDocServiceError("Either content or blocks is required.")


def _share_targets(arguments: dict[str, Any]) -> list[dict[str, Any]]:
    explicit = arguments.get("share_with")
    targets: list[dict[str, Any]] = []
    if isinstance(explicit, list):
        for item in explicit:
            normalized = _normalize_share_target(item)
            if normalized:
                targets.append(normalized)
    if targets:
        return targets

    if settings.default_editor_member_type and settings.default_editor_member_id:
        default_target = _normalize_share_target(
            {
                "member_type": settings.default_editor_member_type,
                "member_id": settings.default_editor_member_id,
                "perm": settings.default_editor_perm,
            }
        )
        if default_target:
            return [default_target]
    return []


def _normalize_share_target(item: Any) -> dict[str, Any] | None:
    if not isinstance(item, dict):
        return None
    member_type = str(item.get("member_type") or item.get("type") or "").strip()
    member_id = str(item.get("member_id") or item.get("id") or "").strip()
    perm = str(item.get("perm") or settings.default_editor_perm or "edit").strip()
    if not member_type or not member_id:
        return None
    if member_type == "open_id":
        member_type = "openid"
    if perm == "editable":
        perm = "edit"
    if perm == "readable":
        perm = "view"
    if perm == "manage":
        perm = "full_access"
    return {
        "member_type": member_type,
        "member_id": member_id,
        "perm": perm,
    }


def _unwrap_data(payload: dict[str, Any]) -> dict[str, Any]:
    data = payload.get("data", {})
    if not isinstance(data, dict):
        raise FeishuDocServiceError("Feishu returned malformed data.")
    return data


def _unwrap_document(payload: dict[str, Any]) -> dict[str, Any]:
    data = _unwrap_data(payload)
    document = data.get("document", data)
    if not isinstance(document, dict):
        raise FeishuDocServiceError("Feishu returned malformed document payload.")
    return document


def _converted_descendants(data: dict[str, Any]) -> tuple[list[str], list[dict[str, Any]]]:
    children_id = data.get("children_id", data.get("childrenIds", []))
    descendants = data.get("descendants", [])
    if isinstance(children_id, list) and isinstance(descendants, list):
        child_ids = [str(item) for item in children_id if str(item).strip()]
        descendant_items = [item for item in descendants if isinstance(item, dict)]
        if child_ids and descendant_items:
            return child_ids, descendant_items

    for candidate_key in ("items", "blocks"):
        candidate = data.get(candidate_key)
        if not isinstance(candidate, list):
            continue
        candidate_items = [item for item in candidate if isinstance(item, dict)]
        child_ids = [
            str(item.get("block_id") or item.get("blockId") or item.get("id") or "").strip()
            for item in candidate_items
        ]
        child_ids = [item for item in child_ids if item]
        if child_ids and candidate_items:
            return child_ids, candidate_items

    raise FeishuDocServiceError(
        f"Converted block payload did not contain usable descendants. data keys={sorted(data.keys())}"
    )


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


def _extract_revision_from_data(payload: dict[str, Any]) -> int | None:
    data = payload.get("data", {})
    if not isinstance(data, dict):
        return None
    revision = data.get("revision_id", data.get("revisionId"))
    try:
        return int(revision) if revision is not None else None
    except (TypeError, ValueError):
        return None


def _doc_url(document_id: str) -> str:
    return f"{settings.doc_base_url}/{document_id}"


def _normalize_text(value: str) -> str:
    return " ".join(value.lower().split())


def _match_heading(blocks: list[dict[str, Any]], heading_query: str, *, document_id: str) -> dict[str, Any] | None:
    query = _normalize_text(heading_query)
    matches = [
        block
        for block in blocks
        if str(block.get("block_type", "")).startswith("heading")
        and _normalize_text(str(block.get("text", ""))) == query
    ]
    if len(matches) == 1:
        return matches[0]
    if len(matches) > 1:
        top_level = [block for block in matches if block.get("parent_id") == document_id]
        if len(top_level) == 1:
            return top_level[0]
        raise FeishuDocServiceError(f"Heading query '{heading_query}' matched multiple headings.")
    partials = [
        block
        for block in blocks
        if str(block.get("block_type", "")).startswith("heading")
        and query in _normalize_text(str(block.get("text", "")))
    ]
    if len(partials) == 1:
        return partials[0]
    if len(partials) > 1:
        raise FeishuDocServiceError(f"Heading query '{heading_query}' is ambiguous.")
    return None


def _index_of_block(blocks: list[dict[str, Any]], block_id: str) -> int:
    for index, block in enumerate(blocks):
        if str(block.get("block_id")) == block_id:
            return index
    return -1


def _matching_text_blocks(blocks: list[dict[str, Any]], find_text: str, heading_query: str = "") -> list[dict[str, Any]]:
    normalized_find = _normalize_text(find_text)
    matched = [
        block
        for block in blocks
        if normalized_find and normalized_find in _normalize_text(str(block.get("text", "")))
    ]
    if not heading_query:
        return matched
    heading = _match_heading(blocks, heading_query, document_id="")
    if heading is None:
        return []
    start = _index_of_block(blocks, str(heading["block_id"]))
    end = len(blocks)
    for index in range(start + 1, len(blocks)):
        block = blocks[index]
        if str(block.get("block_type", "")).startswith("heading"):
            end = index
            break
    scoped_ids = {str(block.get("block_id")) for block in blocks[start:end]}
    return [block for block in matched if str(block.get("block_id")) in scoped_ids]


def _markdown_for_existing_block(block: dict[str, Any], new_text: str) -> str:
    block_type = str(block.get("block_type", "")).strip().lower()
    text = new_text.strip()
    if block_type.startswith("heading"):
        suffix = block_type.replace("heading", "").strip()
        level = int(suffix) if suffix.isdigit() else 1
        level = min(max(level, 1), 6)
        return f"{'#' * level} {text}"
    if block_type == "bullet":
        return f"- {text}"
    if block_type == "ordered":
        return f"1. {text}"
    if block_type == "quote":
        return f"> {text}"
    if block_type == "code":
        return f"```\n{text}\n```"
    return text
