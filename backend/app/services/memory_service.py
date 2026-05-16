from __future__ import annotations

from datetime import datetime, timezone
import re

from sqlalchemy import select

from app.db.session import create_session
from app.models import MessageRecord, SessionMemoryRecord
from app.schemas.memory import SessionMemorySummary

ROLLING_SUMMARY_KIND = "rolling_summary"
GOAL_KIND = "goal"
PROGRESS_KIND = "progress"
CONSTRAINT_KIND = "constraint"
ARTIFACT_KIND = "artifact"
DECISION_KIND = "decision"
OPEN_QUESTION_KIND = "open_question"


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _preview(text: str, limit: int = 180) -> str:
    normalized = " ".join(text.strip().split())
    if len(normalized) <= limit:
        return normalized
    return normalized[: limit - 1] + "…"


def normalize_text(text: str) -> str:
    return " ".join(text.strip().split())


def summarize_for_prompt(text: str, limit: int = 220) -> str:
    normalized = normalize_text(text)
    if len(normalized) <= limit:
        return normalized
    parts = [part.strip() for part in re.split(r"(?<=[。！？.!?])\s+|(?<=;)\s+", normalized) if part.strip()]
    if len(parts) >= 2:
        head = " ".join(parts[:2]).strip()
        tail = parts[-1]
        if head and tail and tail != head:
            summary = f"{head} … {tail}"
            if len(summary) <= limit:
                return summary
        if len(head) <= limit:
            return head
    return normalized[: limit - 1] + "…"


def _to_summary(row: SessionMemoryRecord) -> SessionMemorySummary:
    return SessionMemorySummary(
        id=row.id,
        session_id=row.session_id,
        kind=row.kind,
        content=row.content,
        source_turn_id=row.source_turn_id,
        path_ref=row.path_ref,
        salience=row.salience,
        status=row.status,
        created_at=row.created_at.isoformat(),
        updated_at=row.updated_at.isoformat(),
    )


def _archive_excess_active_memories(session_id: str, kind: str, *, keep: int) -> None:
    with create_session() as db:
        rows = db.scalars(
            select(SessionMemoryRecord)
            .where(
                SessionMemoryRecord.session_id == session_id,
                SessionMemoryRecord.kind == kind,
                SessionMemoryRecord.status == "active",
            )
            .order_by(SessionMemoryRecord.updated_at.desc(), SessionMemoryRecord.id.desc())
        ).all()
        for row in rows[keep:]:
            row.status = "archived"
            row.updated_at = _utcnow()
        db.commit()


def resolve_active_memory(session_id: str, kind: str) -> None:
    with create_session() as db:
        rows = db.scalars(
            select(SessionMemoryRecord).where(
                SessionMemoryRecord.session_id == session_id,
                SessionMemoryRecord.kind == kind,
                SessionMemoryRecord.status == "active",
            )
        ).all()
        for row in rows:
            row.status = "resolved"
            row.updated_at = _utcnow()
        db.commit()


def get_active_memory(session_id: str, kind: str = ROLLING_SUMMARY_KIND) -> str | None:
    with create_session() as db:
        row = db.scalars(
            select(SessionMemoryRecord)
            .where(
                SessionMemoryRecord.session_id == session_id,
                SessionMemoryRecord.kind == kind,
                SessionMemoryRecord.status == "active",
            )
            .order_by(SessionMemoryRecord.updated_at.desc(), SessionMemoryRecord.id.desc())
            .limit(1)
        ).first()
        return row.content if row else None


def upsert_active_memory(
    session_id: str,
    kind: str,
    content: str,
    *,
    source_turn_id: int | None = None,
    salience: int = 80,
    path_ref: str | None = None,
) -> str:
    normalized = normalize_text(content)
    with create_session() as db:
        row = db.scalars(
            select(SessionMemoryRecord)
            .where(
                SessionMemoryRecord.session_id == session_id,
                SessionMemoryRecord.kind == kind,
                SessionMemoryRecord.status == "active",
            )
            .order_by(SessionMemoryRecord.updated_at.desc(), SessionMemoryRecord.id.desc())
            .limit(1)
        ).first()
        now = _utcnow()
        if row is None:
            row = SessionMemoryRecord(
                session_id=session_id,
                kind=kind,
                content=normalized,
                source_turn_id=source_turn_id,
                path_ref=path_ref,
                salience=salience,
                status="active",
                created_at=now,
                updated_at=now,
            )
            db.add(row)
        else:
            row.content = normalized
            row.source_turn_id = source_turn_id
            row.path_ref = path_ref
            row.salience = salience
            row.updated_at = now
        db.commit()
        return normalized


def append_memory(
    session_id: str,
    kind: str,
    content: str,
    *,
    source_turn_id: int | None = None,
    salience: int = 70,
    path_ref: str | None = None,
) -> str:
    normalized = normalize_text(content)
    with create_session() as db:
        now = _utcnow()
        row = SessionMemoryRecord(
            session_id=session_id,
            kind=kind,
            content=normalized,
            source_turn_id=source_turn_id,
            path_ref=path_ref,
            salience=salience,
            status="active",
            created_at=now,
            updated_at=now,
        )
        db.add(row)
        db.commit()
        return normalized


def remember_goal(session_id: str, content: str, *, source_turn_id: int | None = None) -> str:
    return upsert_active_memory(
        session_id,
        GOAL_KIND,
        content,
        source_turn_id=source_turn_id,
        salience=95,
    )


def remember_progress(session_id: str, content: str, *, source_turn_id: int | None = None) -> str:
    return upsert_active_memory(
        session_id,
        PROGRESS_KIND,
        content,
        source_turn_id=source_turn_id,
        salience=85,
    )


def remember_constraint(session_id: str, content: str, *, source_turn_id: int | None = None) -> str:
    return upsert_active_memory(
        session_id,
        CONSTRAINT_KIND,
        content,
        source_turn_id=source_turn_id,
        salience=90,
    )


def remember_artifact(
    session_id: str,
    content: str,
    *,
    source_turn_id: int | None = None,
    path_ref: str | None = None,
) -> str:
    normalized = normalize_text(content)
    if path_ref:
        with create_session() as db:
            row = db.scalars(
                select(SessionMemoryRecord)
                .where(
                    SessionMemoryRecord.session_id == session_id,
                    SessionMemoryRecord.kind == ARTIFACT_KIND,
                    SessionMemoryRecord.status == "active",
                    SessionMemoryRecord.path_ref == path_ref,
                )
                .order_by(SessionMemoryRecord.updated_at.desc(), SessionMemoryRecord.id.desc())
                .limit(1)
            ).first()
            now = _utcnow()
            if row is None:
                row = SessionMemoryRecord(
                    session_id=session_id,
                    kind=ARTIFACT_KIND,
                    content=normalized,
                    source_turn_id=source_turn_id,
                    path_ref=path_ref,
                    salience=75,
                    status="active",
                    created_at=now,
                    updated_at=now,
                )
                db.add(row)
            else:
                row.content = normalized
                row.source_turn_id = source_turn_id
                row.updated_at = now
            db.commit()
        _archive_excess_active_memories(session_id, ARTIFACT_KIND, keep=6)
        return normalized
    value = append_memory(
        session_id,
        ARTIFACT_KIND,
        content,
        source_turn_id=source_turn_id,
        salience=75,
        path_ref=path_ref,
    )
    _archive_excess_active_memories(session_id, ARTIFACT_KIND, keep=6)
    return value


def remember_decision(session_id: str, content: str, *, source_turn_id: int | None = None) -> str:
    value = append_memory(
        session_id,
        DECISION_KIND,
        content,
        source_turn_id=source_turn_id,
        salience=88,
    )
    resolve_active_memory(session_id, OPEN_QUESTION_KIND)
    _archive_excess_active_memories(session_id, DECISION_KIND, keep=3)
    return value


def remember_open_question(session_id: str, content: str, *, source_turn_id: int | None = None) -> str:
    return upsert_active_memory(
        session_id,
        OPEN_QUESTION_KIND,
        content,
        source_turn_id=source_turn_id,
        salience=82,
    )


def list_active_memories(session_id: str, kind: str, *, limit: int = 3) -> list[str]:
    with create_session() as db:
        rows = db.scalars(
            select(SessionMemoryRecord)
            .where(
                SessionMemoryRecord.session_id == session_id,
                SessionMemoryRecord.kind == kind,
                SessionMemoryRecord.status == "active",
            )
            .order_by(SessionMemoryRecord.updated_at.desc(), SessionMemoryRecord.id.desc())
            .limit(limit)
        ).all()
        return [row.content for row in rows]


def list_memory(session_id: str, *, limit: int = 80) -> list[SessionMemorySummary]:
    with create_session() as db:
        rows = db.scalars(
            select(SessionMemoryRecord)
            .where(SessionMemoryRecord.session_id == session_id)
            .order_by(SessionMemoryRecord.updated_at.desc(), SessionMemoryRecord.id.desc())
            .limit(limit)
        ).all()
        return [_to_summary(row) for row in rows]


def refresh_rolling_summary(session_id: str, source_turn_id: int | None = None) -> str:
    with create_session() as db:
        messages = db.scalars(
            select(MessageRecord)
            .where(MessageRecord.session_id == session_id)
            .order_by(MessageRecord.created_at.desc(), MessageRecord.id.desc())
            .limit(10)
        ).all()
        messages = list(reversed(messages))
        user_messages = [message for message in messages if message.role == "user"]
        assistant_messages = [message for message in messages if message.role == "assistant"]

        latest_goal = _preview(user_messages[-1].content, 220) if user_messages else ""
        recent_user = [_preview(message.content, 120) for message in user_messages[-3:]]
        latest_assistant = _preview(assistant_messages[-1].content, 160) if assistant_messages else ""

        lines = ["Session summary:"]
        if latest_goal:
            lines.append(f"- Latest user goal: {latest_goal}")
        if recent_user:
            lines.append("- Recent user requests:")
            lines.extend(f"  - {item}" for item in recent_user)
        if latest_assistant:
            lines.append(f"- Latest assistant result: {latest_assistant}")
        if not latest_goal and not recent_user and not latest_assistant:
            lines.append("- No conversation history yet.")
        content = "\n".join(lines)

        row = db.scalars(
            select(SessionMemoryRecord)
            .where(
                SessionMemoryRecord.session_id == session_id,
                SessionMemoryRecord.kind == ROLLING_SUMMARY_KIND,
                SessionMemoryRecord.status == "active",
            )
            .order_by(SessionMemoryRecord.updated_at.desc(), SessionMemoryRecord.id.desc())
            .limit(1)
        ).first()
        now = _utcnow()
        if row is None:
            row = SessionMemoryRecord(
                session_id=session_id,
                kind=ROLLING_SUMMARY_KIND,
                content=content,
                source_turn_id=source_turn_id,
                salience=100,
                status="active",
                created_at=now,
                updated_at=now,
            )
            db.add(row)
        else:
            row.content = content
            row.source_turn_id = source_turn_id
            row.updated_at = now
        db.commit()
        return content
