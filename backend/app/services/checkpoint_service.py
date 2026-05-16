from __future__ import annotations

import json

from sqlalchemy import select

from app.db.session import create_session
from app.models import TurnCheckpointRecord, TurnRecord


def create_checkpoint(
    turn_id: int,
    *,
    phase: str,
    context: dict[str, object],
    pending_tool_name: str | None = None,
    pending_tool_input: dict[str, object] | None = None,
    summary: str | None = None,
) -> TurnCheckpointRecord:
    with create_session() as db:
        turn = db.get(TurnRecord, turn_id)
        if turn is None:
            raise ValueError(f"Unknown turn {turn_id}")
        next_seq = int(turn.last_checkpoint_seq or 0) + 1
        row = TurnCheckpointRecord(
            turn_id=turn_id,
            checkpoint_seq=next_seq,
            phase=phase,
            context_json=json.dumps(context, ensure_ascii=True),
            pending_tool_name=pending_tool_name,
            pending_tool_input_json=json.dumps(pending_tool_input, ensure_ascii=True) if pending_tool_input is not None else None,
            summary=summary,
        )
        turn.last_checkpoint_seq = next_seq
        db.add(row)
        db.commit()
        db.refresh(row)
        return row


def get_checkpoint(checkpoint_id: int) -> dict[str, object] | None:
    with create_session() as db:
        row = db.get(TurnCheckpointRecord, checkpoint_id)
        if row is None:
            return None
        decoded = json.loads(row.context_json)
        return decoded if isinstance(decoded, dict) else None


def latest_checkpoint(turn_id: int) -> TurnCheckpointRecord | None:
    with create_session() as db:
        return db.scalars(
            select(TurnCheckpointRecord)
            .where(TurnCheckpointRecord.turn_id == turn_id)
            .order_by(TurnCheckpointRecord.checkpoint_seq.desc(), TurnCheckpointRecord.id.desc())
            .limit(1)
        ).first()


def latest_resumable_checkpoint_context(turn_id: int, *, phases: tuple[str, ...] = ("after_tools", "before_model")) -> tuple[TurnCheckpointRecord, dict[str, object]] | None:
    with create_session() as db:
        row = db.scalars(
            select(TurnCheckpointRecord)
            .where(TurnCheckpointRecord.turn_id == turn_id, TurnCheckpointRecord.phase.in_(phases))
            .order_by(TurnCheckpointRecord.checkpoint_seq.desc(), TurnCheckpointRecord.id.desc())
            .limit(1)
        ).first()
        if row is None:
            return None
        decoded = json.loads(row.context_json)
        if not isinstance(decoded, dict):
            return None
        return row, decoded
