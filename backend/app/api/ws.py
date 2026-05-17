from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from app.services.runtime_state import runtime

router = APIRouter()


@router.websocket("/sessions/{session_id}/events")
async def session_events(websocket: WebSocket, session_id: str) -> None:
    if not runtime.session_exists(session_id):
        await websocket.close(code=4404)
        return
    await websocket.accept()
    since_raw = websocket.query_params.get("since_event_id", "").strip()
    last_event_id = int(since_raw) if since_raw.isdigit() else None
    queue = runtime.events.subscribe(session_id)
    try:
        while True:
            durable_events = runtime.list_timeline_since(session_id, after_id=last_event_id, limit=200)
            if not durable_events:
                break
            for event in durable_events:
                await websocket.send_json(event.model_dump())
                if event.event_id is not None:
                    last_event_id = event.event_id
            if len(durable_events) < 200:
                break
        while True:
            event = await queue.get()
            if event.event_id is not None and last_event_id is not None and event.event_id <= last_event_id:
                continue
            await websocket.send_json(event.model_dump())
            if event.event_id is not None:
                last_event_id = event.event_id
    except WebSocketDisconnect:
        pass
    finally:
        runtime.events.unsubscribe(session_id, queue)
