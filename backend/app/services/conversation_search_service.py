from __future__ import annotations

from dataclasses import dataclass
import re

from sqlalchemy import select

from app.db.session import create_session
from app.models import MessageRecord
import app.services.task_service as task_service


@dataclass(frozen=True)
class ConversationHit:
    id: int
    role: str
    content: str
    score: int


def _normalize_text(value: str) -> str:
    return " ".join(value.strip().split())


def _tokenize(value: str) -> list[str]:
    return [token for token in re.split(r"[^a-zA-Z0-9_\-./]+", _normalize_text(value).lower()) if len(token) >= 2]


def _score_message(query: str, tokens: list[str], content: str, recency_rank: int) -> int:
    normalized = _normalize_text(content).lower()
    substring_bonus = 120 if query and query.lower() in normalized else 0
    token_bonus = sum(25 for token in tokens if token in normalized)
    recency_bonus = max(0, 40 - recency_rank)
    return substring_bonus + token_bonus + recency_bonus


def search_conversation(
    session_id: str,
    *,
    query: str,
    role: str | None = None,
    limit: int = 5,
    scan_limit: int = 200,
) -> list[ConversationHit]:
    tokens = _tokenize(query)
    active = task_service.get_active_task(session_id)
    with create_session() as db:
        stmt = (
            select(MessageRecord)
            .where(MessageRecord.session_id == session_id)
            .order_by(MessageRecord.created_at.desc(), MessageRecord.id.desc())
            .limit(scan_limit)
        )
        if active is not None:
            stmt = stmt.where(MessageRecord.task_id == active.id)
        rows = list(db.scalars(stmt).all())

    hits: list[ConversationHit] = []
    for recency_rank, row in enumerate(rows):
        if role and row.role != role:
            continue
        score = _score_message(query, tokens, row.content, recency_rank)
        if score <= 0 and query.strip():
            continue
        hits.append(
            ConversationHit(
                id=row.id,
                role=row.role,
                content=row.content,
                score=score,
            )
        )
    hits.sort(key=lambda item: (item.score, item.id), reverse=True)
    return hits[: max(1, limit)]


def search_conversation_text(
    session_id: str,
    *,
    query: str,
    role: str | None = None,
    limit: int = 5,
) -> str:
    hits = search_conversation(
        session_id,
        query=query,
        role=role,
        limit=limit,
    )
    if not hits:
        return f"No current task conversation history matched query: {query}"
    lines = [
        "Conversation search results:",
        f"Query: {query}",
    ]
    if role:
        lines.append(f"Role filter: {role}")
    for hit in hits:
        content = _normalize_text(hit.content)
        if len(content) > 180:
            content = content[:179] + "…"
        lines.append(f"- [{hit.role}] {content}")
    return "\n".join(lines)
