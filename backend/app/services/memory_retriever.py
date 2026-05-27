from __future__ import annotations

from dataclasses import dataclass
from pathlib import PurePath
import re
from typing import Iterable

from sqlalchemy import select

from app.db.session import create_session
from app.models import SessionMemoryRecord, SessionRecord
import app.services.task_service as task_service


STATUS_PRIORITY = {
    "active": 300,
    "archived": 150,
    "resolved": 0,
}

KIND_PRIORITY = {
    "constraint": 200,
    "goal": 170,
    "decision": 140,
    "progress": 120,
    "open_question": 110,
    "artifact": 100,
    "rolling_summary": 80,
}

PER_KIND_LIMITS = {
    "constraint": 2,
    "goal": 2,
    "decision": 3,
    "progress": 2,
    "open_question": 2,
    "artifact": 4,
}


@dataclass(frozen=True)
class RankedMemory:
    id: int
    kind: str
    content: str
    path_ref: str | None
    source_turn_id: int | None
    status: str
    salience: int
    score: int
    path_match: bool
    text_matches: int


@dataclass(frozen=True)
class RetrievalResult:
    stable: list[RankedMemory]
    dynamic: list[RankedMemory]
    counts_by_kind: dict[str, int]


def _normalize_text(value: str) -> str:
    return " ".join(value.strip().split())


def _tokenize_text(value: str) -> list[str]:
    lowered = _normalize_text(value).lower()
    return [token for token in re.split(r"[^a-zA-Z0-9_\-./]+", lowered) if len(token) >= 2]


def _path_terms(values: Iterable[str]) -> set[str]:
    terms: set[str] = set()
    for value in values:
        normalized = value.strip()
        if not normalized:
            continue
        lowered = normalized.lower()
        terms.add(lowered)
        terms.update(part for part in re.split(r"[\\/]+", lowered) if part)
        try:
            path = PurePath(normalized)
        except Exception:
            continue
        if path.name:
            terms.add(path.name.lower())
    return terms


def _text_match_count(query_tokens: list[str], content: str) -> int:
    if not query_tokens:
        return 0
    normalized = _normalize_text(content).lower()
    return sum(1 for token in query_tokens if token in normalized)


def _path_matches(row_path: str | None, related_path_terms: set[str]) -> bool:
    if not row_path or not related_path_terms:
        return False
    return bool(_path_terms([row_path]) & related_path_terms)


def _row_score(
    row: SessionMemoryRecord,
    *,
    recency_rank: int,
    query_tokens: list[str],
    related_path_terms: set[str],
) -> tuple[int, bool, int]:
    path_match = _path_matches(row.path_ref, related_path_terms)
    text_matches = _text_match_count(query_tokens, row.content)
    status_score = STATUS_PRIORITY.get(row.status, 0)
    kind_score = KIND_PRIORITY.get(row.kind, 0)
    overlap_score = (90 if path_match else 0) + min(text_matches, 4) * 25
    salience_score = int(row.salience or 0)
    recency_score = max(0, 40 - recency_rank)
    score = status_score + kind_score + overlap_score + salience_score + recency_score
    return score, path_match, text_matches


def rank_session_memories(
    session_id: str,
    *,
    task_id: int | None = None,
    query_text: str = "",
    related_paths: Iterable[str] | None = None,
    include_resolved: bool = True,
    limit: int = 120,
) -> list[RankedMemory]:
    query_tokens = _tokenize_text(query_text)
    related_path_terms = _path_terms(related_paths or [])
    with create_session() as db:
        session_row = db.get(SessionRecord, session_id)
        branch_context_id = session_row.branch_context_id if session_row else None
        resolved_task_id = task_id
        if resolved_task_id is None:
            active = task_service.get_active_task(session_id)
            resolved_task_id = active.id if active else None
        stmt = select(SessionMemoryRecord).where(SessionMemoryRecord.session_id == session_id)
        if resolved_task_id is not None:
            stmt = stmt.where(SessionMemoryRecord.task_id == resolved_task_id)
        if branch_context_id is not None:
            stmt = stmt.where(SessionMemoryRecord.branch_context_id == branch_context_id)
        stmt = stmt.order_by(SessionMemoryRecord.updated_at.desc(), SessionMemoryRecord.id.desc()).limit(limit)
        rows = list(db.scalars(stmt).all())

    ranked: list[RankedMemory] = []
    for recency_rank, row in enumerate(rows):
        if not include_resolved and row.status == "resolved":
            continue
        score, path_match, text_matches = _row_score(
            row,
            recency_rank=recency_rank,
            query_tokens=query_tokens,
            related_path_terms=related_path_terms,
        )
        ranked.append(
            RankedMemory(
                id=row.id,
                kind=row.kind,
                content=row.content,
                path_ref=row.path_ref,
                source_turn_id=row.source_turn_id,
                status=row.status,
                salience=int(row.salience or 0),
                score=score,
                path_match=path_match,
                text_matches=text_matches,
            )
        )
    ranked.sort(key=lambda item: (item.score, item.id), reverse=True)
    return ranked


def _kind_limited(rows: list[RankedMemory]) -> list[RankedMemory]:
    limited: list[RankedMemory] = []
    seen_by_kind: dict[str, int] = {}
    for row in rows:
        max_for_kind = PER_KIND_LIMITS.get(row.kind, 2)
        current = seen_by_kind.get(row.kind, 0)
        if current >= max_for_kind:
            continue
        limited.append(row)
        seen_by_kind[row.kind] = current + 1
    return limited


def retrieve_context_memories(
    session_id: str,
    *,
    task_id: int | None = None,
    query_text: str = "",
    related_paths: Iterable[str] | None = None,
) -> RetrievalResult:
    ranked = _kind_limited(
        rank_session_memories(
            session_id,
            task_id=task_id,
            query_text=query_text,
            related_paths=related_paths,
        )
    )

    stable: list[RankedMemory] = []
    dynamic: list[RankedMemory] = []
    used_ids: set[int] = set()
    counts_by_kind: dict[str, int] = {}

    def _add(target: list[RankedMemory], row: RankedMemory) -> None:
        if row.id in used_ids:
            return
        target.append(row)
        used_ids.add(row.id)
        counts_by_kind[row.kind] = counts_by_kind.get(row.kind, 0) + 1

    for kind in ("constraint", "goal", "decision"):
        for row in ranked:
            if row.kind == kind and row.status != "resolved":
                _add(stable, row)

    stable_artifacts = 0
    for row in ranked:
        if row.kind != "artifact" or row.path_match or row.text_matches > 0:
            continue
        if stable_artifacts >= 2:
            break
        _add(stable, row)
        stable_artifacts += 1

    for row in ranked:
        if row.kind in {"progress", "open_question"}:
            _add(dynamic, row)

    for row in ranked:
        if row.path_match or row.text_matches > 0:
            _add(dynamic, row)

    for row in ranked:
        if row.kind == "artifact":
            _add(dynamic, row)

    return RetrievalResult(stable=stable, dynamic=dynamic, counts_by_kind=counts_by_kind)


def search_memories(
    session_id: str,
    *,
    task_id: int | None = None,
    query_text: str,
    kind: str | None = None,
    limit: int = 5,
) -> list[RankedMemory]:
    ranked = rank_session_memories(
        session_id,
        task_id=task_id,
        query_text=query_text,
        related_paths=[],
    )
    if kind:
        ranked = [row for row in ranked if row.kind == kind]
    return ranked[: max(1, limit)]


def format_memory_line(row: RankedMemory, *, limit: int = 180) -> str:
    content = _normalize_text(row.content)
    if len(content) > limit:
        content = content[: limit - 1] + "…"
    suffix: list[str] = []
    if row.path_ref:
        suffix.append(f"path={row.path_ref}")
    if row.source_turn_id:
        suffix.append(f"turn={row.source_turn_id}")
    meta = f" ({', '.join(suffix)})" if suffix else ""
    return f"- [{row.kind}] {content}{meta}"
