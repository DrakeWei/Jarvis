from fastapi import APIRouter, HTTPException

from app.schemas.approvals import ApprovalDecision
from app.schemas.events import MessageCreate, SessionCreate
from app.schemas.tasks import TaskCreate
from app.services import task_service
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
        "tasks": task_service.list_tasks(),
        "approvals": runtime.list_approvals(),
        "tool_executions": runtime.list_tool_executions(),
    }


@router.post("/sessions")
async def create_session(payload: SessionCreate):
    return await runtime.create_session(payload)


@router.get("/sessions")
async def list_sessions():
    return runtime.list_sessions()


@router.get("/sessions/{session_id}/timeline")
async def get_timeline(session_id: str):
    if not runtime.session_exists(session_id):
        raise HTTPException(status_code=404, detail="Unknown session")
    return runtime.list_timeline(session_id)


@router.post("/sessions/{session_id}/messages")
async def post_message(session_id: str, payload: MessageCreate) -> dict[str, str]:
    if not runtime.session_exists(session_id):
        raise HTTPException(status_code=404, detail="Unknown session")
    await runtime.append_message(session_id, payload)
    return {"status": "accepted"}


@router.get("/tasks")
async def list_tasks():
    return task_service.list_tasks()


@router.post("/tasks")
async def create_task(payload: TaskCreate):
    return task_service.create_task(payload)


@router.get("/tool-executions")
async def list_tool_executions(session_id: str | None = None):
    return runtime.list_tool_executions(session_id)


@router.get("/approvals")
async def list_approvals(session_id: str | None = None):
    return runtime.list_approvals(session_id)


@router.post("/approvals/{approval_id}/decision")
async def decide_approval(approval_id: int, payload: ApprovalDecision):
    result = await runtime.decide_approval(
        approval_id,
        approve=payload.approve,
        feedback=payload.feedback,
    )
    if not result:
        raise HTTPException(status_code=404, detail="Unknown approval")
    return result
