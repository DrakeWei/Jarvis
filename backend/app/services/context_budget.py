from __future__ import annotations

from copy import deepcopy
from typing import Any


def derive_budget(max_tokens: int) -> dict[str, int]:
    safe_tokens = max(256, int(max_tokens or 0))
    response_headroom_chars = safe_tokens * 4
    total_budget_chars = safe_tokens * 12
    available_context_chars = max(safe_tokens * 4, total_budget_chars - response_headroom_chars)
    stable_target_chars = int(available_context_chars * 0.35)
    dynamic_target_chars = max(1500, available_context_chars - stable_target_chars)
    return {
        "response_headroom_chars": response_headroom_chars,
        "total_budget_chars": total_budget_chars,
        "available_context_chars": available_context_chars,
        "stable_target_chars": stable_target_chars,
        "dynamic_target_chars": dynamic_target_chars,
    }


def summarize_text(text: str, limit: int) -> str:
    normalized = " ".join(str(text).strip().split())
    if len(normalized) <= limit:
        return normalized
    half = max(24, (limit - 5) // 2)
    return f"{normalized[:half]} ... {normalized[-half:]}"


def estimate_message_size(message: dict[str, Any]) -> int:
    content = message.get("content", "")
    if isinstance(content, str):
        return len(content)
    if not isinstance(content, list):
        return len(str(content))
    total = 0
    for part in content:
        if isinstance(part, dict):
            total += len(str(part.get("content", "")))
            total += len(str(part.get("text", "")))
            total += len(str(part.get("name", "")))
        else:
            total += len(str(part))
    return total


def estimate_messages_size(messages: list[dict[str, Any]]) -> int:
    return sum(estimate_message_size(message) for message in messages)


def build_tool_result_summary(
    *,
    tool_name: str,
    content: str,
    status: str = "completed",
    limit: int = 800,
) -> str:
    normalized = str(content).strip()
    if len(normalized) <= limit:
        return normalized
    lines = [line.strip() for line in normalized.splitlines() if line.strip()]
    interesting = [line for line in lines if "/" in line or "." in line][:4]
    head = lines[:3]
    tail = lines[-2:] if len(lines) > 5 else []
    kept: list[str] = []
    for line in head + interesting + tail:
        if line and line not in kept:
            kept.append(line)
    body = "\n".join(kept) if kept else summarize_text(normalized, max(120, limit - 120))
    summary = (
        f"[Compacted {tool_name} result | status={status} | original_chars={len(normalized)}]\n"
        f"{body}\n"
        "Full tool output was shortened for context budget reasons."
    )
    if len(summary) <= limit:
        return summary
    return summarize_text(summary, limit)


def compact_tool_result_messages(
    messages: list[dict[str, Any]],
    tool_name_by_id: dict[str, str],
    *,
    per_result_limit: int = 900,
) -> tuple[list[dict[str, Any]], int]:
    copied = deepcopy(messages)
    summarized = 0
    for message in copied:
        content = message.get("content")
        if message.get("role") != "user" or not isinstance(content, list):
            continue
        rewritten: list[Any] = []
        changed = False
        for part in content:
            if not isinstance(part, dict) or part.get("type") != "tool_result":
                rewritten.append(part)
                continue
            result_text = str(part.get("content", ""))
            if len(result_text) <= per_result_limit:
                rewritten.append(part)
                continue
            tool_use_id = str(part.get("tool_use_id", ""))
            tool_name = tool_name_by_id.get(tool_use_id, "tool")
            rewritten.append(
                {
                    **part,
                    "content": build_tool_result_summary(
                        tool_name=tool_name,
                        content=result_text,
                        limit=per_result_limit,
                    ),
                }
            )
            summarized += 1
            changed = True
        if changed:
            message["content"] = rewritten
    return copied, summarized
