from fastapi import APIRouter, File, HTTPException, UploadFile

from app.core.config import settings
from app.schemas.approvals import ApprovalDecision
from app.schemas.events import MessageCreate, SessionCreate, SessionRename
from app.schemas.subagents import SubagentRunCreate
from app.schemas.tasks import TaskCreate
from app.schemas.teammates import TeammateCreate, TeammateMessageCreate
from app.schemas.workspace import WorkspaceResolveRequest
from app.services import task_service
from app.services.runtime_state import runtime

router = APIRouter()


@router.get("/health")
async def health() -> dict[str, str | bool]:
    summary = runtime.observability_summary()
    return {
        "status": "ok",
        "dispatcher_running": runtime.dispatcher_running(),
        "runtime_role": summary.runtime_role,
        "configured_event_bus_backend": summary.configured_event_bus_backend,
        "effective_event_bus_backend": summary.effective_event_bus_backend,
    }


@router.get("/observability/runtime")
async def runtime_observability():
    return runtime.observability_summary()


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


@router.post("/workspaces/resolve")
async def resolve_workspace(payload: WorkspaceResolveRequest):
    return runtime.resolve_workspace(payload.content)


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


@router.get("/sessions/{session_id}/state")
async def get_session_state(session_id: str):
    result = runtime.get_session_state(session_id)
    if not result:
        raise HTTPException(status_code=404, detail="Unknown session")
    return result


@router.post("/sessions/{session_id}/assets")
async def upload_session_assets(session_id: str, files: list[UploadFile] = File(...)):
    if not runtime.session_exists(session_id):
        raise HTTPException(status_code=404, detail="Unknown session")
    if not files:
        raise HTTPException(status_code=400, detail="No files uploaded")
    if len(files) > settings.jarvis_asset_max_upload_count:
        raise HTTPException(status_code=400, detail="Too many files in one upload request")
    try:
        return await runtime.upload_asset_streams(session_id, files)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/sessions/{session_id}/assets")
async def list_session_assets(session_id: str):
    if not runtime.session_exists(session_id):
        raise HTTPException(status_code=404, detail="Unknown session")
    return runtime.list_assets(session_id)


@router.get("/sessions/{session_id}/assets/{asset_id}")
async def get_session_asset(session_id: str, asset_id: str):
    if not runtime.session_exists(session_id):
        raise HTTPException(status_code=404, detail="Unknown session")
    asset = runtime.get_asset(session_id, asset_id)
    if not asset:
        raise HTTPException(status_code=404, detail="Unknown asset")
    return asset


@router.delete("/sessions/{session_id}/assets/{asset_id}")
async def delete_session_asset(session_id: str, asset_id: str) -> dict[str, bool]:
    if not runtime.session_exists(session_id):
        raise HTTPException(status_code=404, detail="Unknown session")
    deleted = await runtime.delete_asset(session_id, asset_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="Unknown asset")
    return {"deleted": True}


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


@router.get("/background-jobs")
async def list_background_jobs(
    session_id: str | None = None,
    job_type: str | None = None,
    status: str | None = None,
    limit: int = 100,
):
    return runtime.list_background_jobs(
        session_id=session_id,
        job_type=job_type,
        status=status,
        limit=limit,
    )


@router.get("/background-jobs/{job_id}")
async def get_background_job(job_id: int):
    result = runtime.get_background_job(job_id)
    if not result:
        raise HTTPException(status_code=404, detail="Unknown background job")
    return result


@router.get("/execution-leases")
async def list_execution_leases(scope_type: str | None = None, status: str | None = None):
    return runtime.list_execution_leases(scope_type=scope_type, status=status)


@router.post("/execution-leases/{lease_id}/force-release")
async def force_release_execution_lease(lease_id: int):
    result = runtime.force_release_execution_lease(lease_id)
    if not result:
        raise HTTPException(status_code=404, detail="Unknown execution lease")
    return result


@router.post("/background-jobs/{job_id}/retry")
async def retry_background_job(job_id: int):
    try:
        result = await runtime.retry_background_job(job_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if not result:
        raise HTTPException(status_code=404, detail="Unknown background job")
    return result


@router.post("/background-jobs/{job_id}/cancel")
async def cancel_background_job(job_id: int):
    result = await runtime.cancel_background_job(job_id)
    if not result:
        raise HTTPException(status_code=404, detail="Unknown background job")
    return result


@router.get("/session-memory")
async def list_session_memory(session_id: str):
    if not runtime.session_exists(session_id):
        raise HTTPException(status_code=404, detail="Unknown session")
    return runtime.list_memory(session_id)


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
    try:
        return await runtime.run_subagent(payload)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/turns")
async def list_turns(session_id: str | None = None):
    return runtime.list_turns(session_id)


@router.get("/turns/{turn_id}")
async def get_turn(turn_id: int):
    result = runtime.get_turn(turn_id)
    if not result:
        raise HTTPException(status_code=404, detail="Unknown turn")
    return result


@router.post("/turns/{turn_id}/resume")
async def resume_turn(turn_id: int) -> dict[str, bool]:
    resumed = await runtime.resume_turn(turn_id)
    if not resumed:
        raise HTTPException(status_code=400, detail="Turn is not resumable")
    return {"accepted": True}
