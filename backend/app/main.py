from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.routes import router as api_router
from app.api.ws import router as ws_router
from app.core.config import settings
from app.db.session import init_db
from app.schemas.events import SessionCreate
from app.services.runtime_state import runtime

app = FastAPI(title=settings.app_name)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(api_router, prefix=settings.api_prefix)
app.include_router(ws_router, prefix=settings.api_prefix)


async def initialize_runtime_for_role() -> None:
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    init_db()
    runtime.restore_state()
    if settings.jarvis_runtime_role in {"hybrid", "worker"}:
        runtime.start_dispatcher()
    if settings.jarvis_runtime_role in {"api", "hybrid"} and not runtime.list_sessions():
        await runtime.create_session(SessionCreate(title="Command Deck"))


@app.on_event("startup")
async def startup() -> None:
    await initialize_runtime_for_role()
