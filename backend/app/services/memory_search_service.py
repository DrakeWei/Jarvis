from __future__ import annotations

from app.services import memory_retriever
import app.services.task_service as task_service


def search_memory_text(
    session_id: str,
    *,
    query: str,
    kind: str | None = None,
    limit: int = 5,
) -> str:
    active = task_service.get_active_task(session_id)
    rows = memory_retriever.search_memories(
        session_id,
        task_id=active.id if active else None,
        query_text=query,
        kind=kind,
        limit=limit,
    )
    if not rows:
        return f"No current task memories matched query: {query}"
    lines = [
        "Memory search results:",
        f"Query: {query}",
    ]
    if kind:
        lines.append(f"Kind filter: {kind}")
    lines.extend(memory_retriever.format_memory_line(row) for row in rows)
    return "\n".join(lines)
