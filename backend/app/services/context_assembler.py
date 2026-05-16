from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from pathlib import Path
import re
from typing import Any

from app.core.config import settings
from app.services import session_service
from app.services import memory_retriever
from app.services.context_budget import (
    compact_tool_result_messages,
    derive_budget,
    estimate_messages_size,
    summarize_text,
)


@dataclass(frozen=True)
class AssembledContext:
    system_prompt: str
    messages: list[dict[str, Any]]
    debug_meta: dict[str, Any]


def _normalize_text(value: str) -> str:
    return " ".join(value.strip().split())


def _excerpt(value: str, limit: int = 180) -> str:
    normalized = _normalize_text(value)
    if len(normalized) <= limit:
        return normalized
    return normalized[: limit - 1] + "…"


def _path_terms_from_text(value: str) -> set[str]:
    terms: set[str] = set()
    for token in re.findall(r"[A-Za-z0-9_./\\-]+", value):
        lowered = token.lower()
        if "/" in lowered or "\\" in lowered or "." in lowered:
            terms.add(lowered)
            terms.update(part for part in re.split(r"[\\/]+", lowered) if part)
    return terms


def _content_text(content: Any) -> str:
    if isinstance(content, str):
        return content
    if not isinstance(content, list):
        return str(content)
    parts: list[str] = []
    for part in content:
        if isinstance(part, dict):
            if part.get("type") == "text" and part.get("text"):
                parts.append(str(part["text"]))
            elif part.get("type") == "tool_result" and part.get("content"):
                parts.append(str(part["content"]))
        else:
            parts.append(str(part))
    return "\n".join(parts)


def _latest_user_text(messages: list[dict[str, Any]]) -> str:
    for message in reversed(messages):
        if message.get("role") != "user":
            continue
        content = message.get("content")
        if isinstance(content, str) and content.strip():
            return content
        if isinstance(content, list):
            texts = [
                str(part.get("text", ""))
                for part in content
                if isinstance(part, dict) and part.get("type") == "text" and str(part.get("text", "")).strip()
            ]
            if texts:
                return "\n".join(texts)
    return ""


def _tool_name_by_id(messages: list[dict[str, Any]]) -> dict[str, str]:
    mapping: dict[str, str] = {}
    for message in messages:
        if message.get("role") != "assistant":
            continue
        content = message.get("content")
        if not isinstance(content, list):
            continue
        for part in content:
            if not isinstance(part, dict) or part.get("type") != "tool_use":
                continue
            mapping[str(part.get("id", ""))] = str(part.get("name", "tool"))
    return mapping


def _collect_related_paths(
    messages: list[dict[str, Any]],
    allowed_external_reads: list[Path] | None,
) -> list[str]:
    terms: set[str] = set()
    for path in allowed_external_reads or []:
        terms.add(path.as_posix())
        terms.add(path.name)
    for message in messages[-8:]:
        text = _content_text(message.get("content", ""))
        terms.update(_path_terms_from_text(text))
        content = message.get("content")
        if message.get("role") != "assistant" or not isinstance(content, list):
            continue
        for part in content:
            if not isinstance(part, dict) or part.get("type") != "tool_use":
                continue
            tool_input = part.get("input")
            if not isinstance(tool_input, dict):
                continue
            for value in tool_input.values():
                if isinstance(value, str):
                    terms.update(_path_terms_from_text(value))
    return sorted(term for term in terms if term)


def _select_transcript_indices(messages: list[dict[str, Any]], *, keep: int) -> list[int]:
    if len(messages) <= keep:
        return list(range(len(messages)))
    tail_keep = min(8, keep)
    indices = set(range(len(messages) - tail_keep, len(messages)))
    candidates: list[tuple[int, int]] = []
    for index, message in enumerate(messages[:-tail_keep]):
        content = str(message.get("content", ""))
        score = index
        if message.get("role") == "user":
            score += 30
        if "/" in content or "\\" in content or "." in content:
            score += 20
        if "?" in content or "？" in content:
            score += 10
        if 20 <= len(content) <= 320:
            score += 10
        candidates.append((score, index))
    extra = max(0, keep - tail_keep)
    for _, index in sorted(candidates, reverse=True)[:extra]:
        indices.add(index)
    return sorted(indices)


def build_initial_loop_messages(
    session_id: str,
    *,
    lookback: int = 24,
    keep: int = 12,
) -> list[dict[str, Any]]:
    transcript = session_service.list_message_records(session_id, limit=lookback)
    selected_indices = _select_transcript_indices(transcript, keep=keep)
    return [deepcopy(transcript[index]) for index in selected_indices]


def _find_preserved_suffix_start(messages: list[dict[str, Any]]) -> int:
    if not messages:
        return 0
    last = messages[-1]
    content = last.get("content")
    if last.get("role") == "user" and isinstance(content, list) and any(
        isinstance(part, dict) and part.get("type") == "tool_result" for part in content
    ):
        return max(0, len(messages) - 2)
    return max(0, len(messages) - 4)


def _workspace_facts(
    workspace: Path,
    allowed_external_reads: list[Path] | None,
    retrieval: memory_retriever.RetrievalResult,
) -> tuple[list[str], list[str]]:
    stable_lines: list[str] = []
    external_lines = [
        f"- {path.as_posix()}"
        for path in (allowed_external_reads or [])[:4]
    ]
    artifact_lines = []
    for row in retrieval.stable:
        if row.kind == "artifact" and row.path_ref:
            artifact_lines.append(f"- {row.path_ref}")
    return stable_lines + artifact_lines[:2], external_lines


def _summarize_prefix_messages(
    prefix: list[dict[str, Any]],
    tool_name_by_id: dict[str, str],
) -> list[str]:
    lines: list[str] = []
    for message in prefix[-10:]:
        role = str(message.get("role", ""))
        content = message.get("content", "")
        if isinstance(content, str):
            if content.strip():
                lines.append(f"[{role}] {_excerpt(content, 160)}")
            continue
        if not isinstance(content, list):
            lines.append(f"[{role}] {_excerpt(str(content), 160)}")
            continue
        if role == "assistant":
            tool_names = [
                str(part.get("name", "tool"))
                for part in content
                if isinstance(part, dict) and part.get("type") == "tool_use"
            ]
            text_bits = [
                str(part.get("text", ""))
                for part in content
                if isinstance(part, dict) and part.get("type") == "text" and str(part.get("text", "")).strip()
            ]
            if text_bits:
                lines.append(f"[assistant] {_excerpt(' '.join(text_bits), 140)}")
            if tool_names:
                joined = ", ".join(tool_names[:4])
                lines.append(f"[assistant] called tools: {joined}")
            continue
        if role == "user":
            for part in content:
                if not isinstance(part, dict):
                    continue
                if part.get("type") == "tool_result":
                    tool_name = tool_name_by_id.get(str(part.get("tool_use_id", "")), "tool")
                    lines.append(
                        f"[tool:{tool_name}] {_excerpt(str(part.get('content', '')), 140)}"
                    )
                elif part.get("type") == "text" and str(part.get("text", "")).strip():
                    lines.append(f"[user] {_excerpt(str(part.get('text', '')), 140)}")
    return lines


def _format_stable_block(session_id: str, workspace: Path, retrieval: memory_retriever.RetrievalResult) -> str:
    session = session_service.get_session(session_id)
    if session is None:
        return ""
    lines = [
        f"Workspace mode: {session.workspace_mode}",
        f"Workspace label: {session.workspace_label}",
        f"Workspace root: {workspace.as_posix()}",
    ]
    stable_memory_lines = [memory_retriever.format_memory_line(row, limit=180) for row in retrieval.stable]
    if stable_memory_lines:
        lines.append("Stable session memory:")
        lines.extend(stable_memory_lines)
    block = "\n".join(lines).strip()
    if not block:
        return ""
    return f"<stable-session-context>\n{block}\n</stable-session-context>"


def _render_section(title: str, lines: list[str]) -> str:
    filtered = [line for line in lines if str(line).strip()]
    if not filtered:
        return ""
    return f"{title}:\n" + "\n".join(filtered)


def _prepend_context_text(
    messages: list[dict[str, Any]],
    context_text: str,
) -> list[dict[str, Any]]:
    if not context_text.strip():
        return messages
    copied = deepcopy(messages)
    preferred_index: int | None = None
    fallback_index: int | None = None
    for index, message in enumerate(copied):
        if message.get("role") != "user":
            continue
        fallback_index = index
        content = message.get("content")
        if isinstance(content, list) and any(
            isinstance(part, dict) and part.get("type") == "tool_result"
            for part in content
        ):
            preferred_index = index
            break
    target_index = preferred_index if preferred_index is not None else fallback_index
    if target_index is not None:
        message = copied[target_index]
        content = message.get("content")
        if isinstance(content, str):
            message["content"] = f"{context_text}\n\n{content}".strip()
            return copied
        if isinstance(content, list):
            message["content"] = [{"type": "text", "text": context_text}] + content
            return copied
    return [{"role": "user", "content": context_text}] + copied


def assemble_context(
    *,
    session_id: str,
    workspace: Path,
    messages: list[dict[str, Any]],
    base_system_prompt: str,
    allowed_external_reads: list[Path] | None,
    max_tokens: int | None = None,
) -> AssembledContext:
    if not messages:
        return AssembledContext(
            system_prompt=base_system_prompt,
            messages=[],
            debug_meta={"original_size": len(base_system_prompt), "compacted_size": len(base_system_prompt)},
        )

    budget = derive_budget(max_tokens or settings.llm_max_tokens)
    latest_user_text = _latest_user_text(messages)
    related_paths = _collect_related_paths(messages, allowed_external_reads)
    retrieval = memory_retriever.retrieve_context_memories(
        session_id,
        query_text=latest_user_text,
        related_paths=related_paths,
    )
    stable_block = _format_stable_block(session_id, workspace, retrieval)
    system_prompt = base_system_prompt if not stable_block else f"{base_system_prompt}\n\n{stable_block}"

    suffix_start = _find_preserved_suffix_start(messages)
    prefix = messages[:suffix_start]
    suffix = deepcopy(messages[suffix_start:])
    working_suffix = deepcopy(suffix)
    tool_name_by_id = _tool_name_by_id(messages)
    prefix_lines = _summarize_prefix_messages(prefix, tool_name_by_id)
    stable_workspace_lines, external_lines = _workspace_facts(workspace, allowed_external_reads, retrieval)
    dynamic_memory_lines = [memory_retriever.format_memory_line(row, limit=160) for row in retrieval.dynamic]

    dropped_blocks: list[str] = []
    summarized_tool_results = 0

    def _dynamic_text(
        *,
        include_external: bool,
        include_short_term: bool,
        memory_lines: list[str],
    ) -> str:
        sections = [
            _render_section("Workspace facts", stable_workspace_lines),
            _render_section("External read references", external_lines if include_external else []),
            _render_section("Relevant session memory", memory_lines),
            _render_section("Earlier turn summary", prefix_lines if include_short_term else []),
        ]
        body = "\n\n".join(section for section in sections if section)
        if not body:
            return ""
        return f"<runtime-context>\n{body}\n</runtime-context>"

    include_external = True
    include_short_term = True
    current_memory_lines = list(dynamic_memory_lines)
    dynamic_text = _dynamic_text(
        include_external=include_external,
        include_short_term=include_short_term,
        memory_lines=current_memory_lines,
    )
    assembled_messages = _prepend_context_text(working_suffix, dynamic_text)

    def _estimate_total(candidate_messages: list[dict[str, Any]]) -> int:
        return len(system_prompt) + estimate_messages_size(candidate_messages)

    total_size = _estimate_total(assembled_messages)
    if total_size > budget["available_context_chars"] and include_external and external_lines:
        include_external = False
        dropped_blocks.append("external_read_references")
        dynamic_text = _dynamic_text(
            include_external=include_external,
            include_short_term=include_short_term,
            memory_lines=current_memory_lines,
        )
        assembled_messages = _prepend_context_text(working_suffix, dynamic_text)
        total_size = _estimate_total(assembled_messages)

    if total_size > budget["available_context_chars"] and len(current_memory_lines) > 4:
        current_memory_lines = current_memory_lines[:4]
        dropped_blocks.append("low_priority_session_memory")
        dynamic_text = _dynamic_text(
            include_external=include_external,
            include_short_term=include_short_term,
            memory_lines=current_memory_lines,
        )
        assembled_messages = _prepend_context_text(working_suffix, dynamic_text)
        total_size = _estimate_total(assembled_messages)

    if total_size > budget["available_context_chars"]:
        working_suffix, summarized_tool_results = compact_tool_result_messages(
            working_suffix,
            tool_name_by_id,
            per_result_limit=900,
        )
        dynamic_text = _dynamic_text(
            include_external=include_external,
            include_short_term=include_short_term,
            memory_lines=current_memory_lines,
        )
        assembled_messages = _prepend_context_text(working_suffix, dynamic_text)
        total_size = _estimate_total(assembled_messages)

    if total_size > budget["available_context_chars"] and include_short_term and prefix_lines:
        include_short_term = False
        dropped_blocks.append("older_turn_summary")
        dynamic_text = _dynamic_text(
            include_external=include_external,
            include_short_term=include_short_term,
            memory_lines=current_memory_lines,
        )
        assembled_messages = _prepend_context_text(working_suffix, dynamic_text)
        total_size = _estimate_total(assembled_messages)

    if total_size > budget["available_context_chars"] and dynamic_text:
        trimmed = summarize_text(dynamic_text, budget["dynamic_target_chars"])
        dropped_blocks.append("trimmed_dynamic_context")
        assembled_messages = _prepend_context_text(working_suffix, trimmed)
        total_size = _estimate_total(assembled_messages)

    return AssembledContext(
        system_prompt=system_prompt,
        messages=assembled_messages,
        debug_meta={
            "original_size": len(base_system_prompt) + estimate_messages_size(messages),
            "compacted_size": total_size,
            "dropped_blocks": dropped_blocks,
            "summarized_tool_results": summarized_tool_results,
            "retrieved_memory_counts": retrieval.counts_by_kind,
            "budget": budget,
        },
    )
