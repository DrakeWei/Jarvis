from __future__ import annotations

import json
from dataclasses import dataclass

from sqlalchemy import select

from app.db.session import create_session
from app.models import TurnReflectionRecord


@dataclass(frozen=True)
class TurnReflectionSummary:
    id: int
    turn_id: int
    checkpoint_id: int | None
    reflection_seq: int
    verdict: str
    reason_codes: list[str]
    next_action_prompt: str | None
    summary: str
    created_at: str


def _decode_reason_codes(value: str | None) -> list[str]:
    if not value:
        return []
    try:
        decoded = json.loads(value)
    except Exception:
        return []
    if not isinstance(decoded, list):
        return []
    return [str(item) for item in decoded if str(item).strip()]


def _to_summary(row: TurnReflectionRecord) -> TurnReflectionSummary:
    return TurnReflectionSummary(
        id=row.id,
        turn_id=row.turn_id,
        checkpoint_id=row.checkpoint_id,
        reflection_seq=row.reflection_seq,
        verdict=row.verdict,
        reason_codes=_decode_reason_codes(row.reason_codes_json),
        next_action_prompt=row.next_action_prompt,
        summary=row.summary,
        created_at=row.created_at.isoformat(),
    )


def create_reflection(
    turn_id: int,
    *,
    checkpoint_id: int | None,
    verdict: str,
    reason_codes: list[str],
    next_action_prompt: str | None,
    summary: str,
) -> TurnReflectionSummary:
    with create_session() as db:
        latest_seq = db.scalars(
            select(TurnReflectionRecord.reflection_seq)
            .where(TurnReflectionRecord.turn_id == turn_id)
            .order_by(TurnReflectionRecord.reflection_seq.desc(), TurnReflectionRecord.id.desc())
            .limit(1)
        ).first()
        row = TurnReflectionRecord(
            turn_id=turn_id,
            checkpoint_id=checkpoint_id,
            reflection_seq=int(latest_seq or 0) + 1,
            verdict=verdict,
            reason_codes_json=json.dumps(reason_codes, ensure_ascii=True),
            next_action_prompt=next_action_prompt,
            summary=summary,
        )
        db.add(row)
        db.commit()
        db.refresh(row)
        return _to_summary(row)


def latest_turn_reflection(turn_id: int) -> TurnReflectionSummary | None:
    with create_session() as db:
        row = db.scalars(
            select(TurnReflectionRecord)
            .where(TurnReflectionRecord.turn_id == turn_id)
            .order_by(TurnReflectionRecord.reflection_seq.desc(), TurnReflectionRecord.id.desc())
            .limit(1)
        ).first()
        return _to_summary(row) if row else None


def list_turn_reflections(turn_id: int) -> list[TurnReflectionSummary]:
    with create_session() as db:
        rows = db.scalars(
            select(TurnReflectionRecord)
            .where(TurnReflectionRecord.turn_id == turn_id)
            .order_by(TurnReflectionRecord.reflection_seq.asc(), TurnReflectionRecord.id.asc())
        ).all()
        return [_to_summary(row) for row in rows]
