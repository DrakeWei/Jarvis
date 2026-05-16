from pydantic import BaseModel

from app.schemas.events import SessionSummary
from app.schemas.turns import TurnSummary


class SessionStateSummary(BaseModel):
    session: SessionSummary
    active_turn: TurnSummary | None = None
    latest_interrupted_turn: TurnSummary | None = None
    latest_waiting_approval_turn: TurnSummary | None = None
    rolling_summary: str | None = None
