from __future__ import annotations

from app.runtime.manager import RuntimeManager
from app.schemas.events import MessageCreate, SessionCreate


class RuntimeManagerEvalAdapter:
    def __init__(self, runtime: RuntimeManager) -> None:
        self.runtime = runtime

    async def create_session(self, payload: SessionCreate):
        return await self.runtime.create_session(payload)

    async def append_message(self, session_id: str, payload: MessageCreate) -> None:
        await self.runtime.append_message(session_id, payload)

    def get_session_state(self, session_id: str):
        return self.runtime.get_session_state(session_id)

    def list_timeline(self, session_id: str):
        return self.runtime.list_timeline(session_id)

    def list_tool_executions(self, session_id: str | None = None):
        return self.runtime.list_tool_executions(session_id)

    def list_approvals(self, session_id: str | None = None):
        return self.runtime.list_approvals(session_id)

    def list_turns(self, session_id: str | None = None):
        return self.runtime.list_turns(session_id)

    async def decide_approval(self, approval_id: int, approve: bool, feedback: str = ""):
        return await self.runtime.decide_approval(approval_id, approve=approve, feedback=feedback)
