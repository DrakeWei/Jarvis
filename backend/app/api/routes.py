from fastapi import APIRouter, HTTPException

from app.schemas.approvals import ApprovalDecision
from app.schemas.events import MessageCreate, SessionCreate, SessionRename
from app.schemas.subagents import SubagentRunCreate
from app.schemas.tasks import TaskCreate
from app.schemas.teammates import TeammateCreate, TeammateMessageCreate
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
        "teammates": runtime.list_teammates(),
        "subagents": runtime.list_subagents(),
        "approvals": runtime.list_approvals(),
        "tool_executions": runtime.list_tool_executions(),
    }


@router.get("/skills")
async def list_skills():
    return runtime.list_local_skills()


@router.post("/sessions")
async def create_session(payload: SessionCreate):
    return await runtime.create_session(payload)


@router.get("/sessions")
async def list_sessions():
    return runtime.list_sessions()


@router.patch("/sessions/{session_id}")
async def rename_session(session_id: str, payload: SessionRename):
    result = await runtime.rename_session(session_id, payload.title)
    if not result:
        raise HTTPException(status_code=404, detail="Unknown session")
    return result


@router.delete("/sessions/{session_id}")
async def delete_session(session_id: str) -> dict[str, bool]:
    deleted = await runtime.soft_delete_session(session_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="Unknown session")
    return {"deleted": True}


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


@router.post("/sessions/{session_id}/stop")
async def stop_session_turn(session_id: str) -> dict[str, bool]:
    if not runtime.session_exists(session_id):
        raise HTTPException(status_code=404, detail="Unknown session")
    stopped = await runtime.cancel_session_turn(session_id)
    return {"stopped": stopped}


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


@router.get("/teammates")
async def list_teammates(session_id: str | None = None):
    return runtime.list_teammates(session_id)


@router.post("/teammates")
async def create_teammate(payload: TeammateCreate):
    if not runtime.session_exists(payload.session_id):
        raise HTTPException(status_code=404, detail="Unknown session")
    return await runtime.create_teammate(payload)


@router.get("/teammates/{agent_id}/messages")
async def list_teammate_messages(agent_id: int):
    return runtime.list_teammate_messages(agent_id)


@router.post("/teammates/{agent_id}/messages")
async def send_teammate_message(agent_id: int, payload: TeammateMessageCreate):
    result = await runtime.send_teammate_message(agent_id, payload.content)
    if not result:
        raise HTTPException(status_code=404, detail="Unknown teammate")
    return result


@router.get("/subagents")
async def list_subagents(session_id: str | None = None):
    return runtime.list_subagents(session_id)


@router.post("/subagents")
async def run_subagent(payload: SubagentRunCreate):
    if not runtime.session_exists(payload.session_id):
        raise HTTPException(status_code=404, detail="Unknown session")
    return await runtime.run_subagent(payload)
