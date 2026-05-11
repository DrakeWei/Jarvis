from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.routes import router as api_router
from app.api.ws import router as ws_router
from app.core.config import settings
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


@app.on_event("startup")
async def startup() -> None:
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    if not runtime.sessions:
        await runtime.create_session(SessionCreate(title="Command Deck"))
