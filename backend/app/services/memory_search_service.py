from __future__ import annotations

from app.services import memory_retriever


def search_memory_text(
    session_id: str,
    *,
    query: str,
    kind: str | None = None,
    limit: int = 5,
) -> str:
    rows = memory_retriever.search_memories(
        session_id,
        query_text=query,
        kind=kind,
        limit=limit,
    )
    if not rows:
        return f"No session memories matched query: {query}"
    lines = [
        "Memory search results:",
        f"Query: {query}",
    ]
    if kind:
        lines.append(f"Kind filter: {kind}")
    lines.extend(memory_retriever.format_memory_line(row) for row in rows)
    return "\n".join(lines)
