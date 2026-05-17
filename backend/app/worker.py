from __future__ import annotations

import asyncio

from app.core.config import settings
from app.main import initialize_runtime_for_role
from app.services.runtime_state import runtime


async def run_worker_forever() -> None:
    if settings.jarvis_runtime_role == "api":
        raise RuntimeError("Worker entrypoint cannot run with JARVIS_RUNTIME_ROLE=api.")
    await initialize_runtime_for_role()
    stop_event = asyncio.Event()
    try:
        await stop_event.wait()
    finally:
        await runtime.stop_dispatcher()


def main() -> None:
    asyncio.run(run_worker_forever())
