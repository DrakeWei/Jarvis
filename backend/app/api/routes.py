from fastapi import APIRouter, HTTPException

from app.schemas.events import MessageCreate, SessionCreate
from app.services.runtime_state import runtime

router = APIRouter()


@router.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}


@router.get("/bootstrap")
async def bootstrap() -> dict[str, object]:
    return {
        "app": "Jarvis Agent Cockpit",
        "sessions": runtime.list_sessions(),
        "panels": ["tasks", "teammates", "approvals", "logs"],
    }


@router.post("/sessions")
async def create_session(payload: SessionCreate):
    return await runtime.create_session(payload)


@router.get("/sessions")
async def list_sessions():
    return runtime.list_sessions()


@router.post("/sessions/{session_id}/messages")
async def post_message(session_id: str, payload: MessageCreate) -> dict[str, str]:
    if session_id not in runtime.sessions:
        raise HTTPException(status_code=404, detail="Unknown session")
    await runtime.append_message(session_id, payload)
    return {"status": "accepted"}
