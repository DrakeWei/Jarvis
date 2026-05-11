from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from app.services.runtime_state import runtime

router = APIRouter()


@router.websocket("/sessions/{session_id}/events")
async def session_events(websocket: WebSocket, session_id: str) -> None:
    if not runtime.session_exists(session_id):
        await websocket.close(code=4404)
        return
    await websocket.accept()
    queue = runtime.events.subscribe(session_id)
    try:
        while True:
            event = await queue.get()
            await websocket.send_json(event.model_dump())
    except WebSocketDisconnect:
        runtime.events.unsubscribe(session_id, queue)
