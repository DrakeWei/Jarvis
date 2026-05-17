from __future__ import annotations

from pathlib import Path
from unittest import IsolatedAsyncioTestCase
from unittest.mock import AsyncMock, patch

import app.main as app_main


class RuntimeRoleInitializationTests(IsolatedAsyncioTestCase):
    async def test_api_role_skips_dispatcher_and_bootstraps_default_session(self) -> None:
        with patch.object(app_main.settings, "jarvis_runtime_role", "api"), patch.object(
            app_main.settings,
            "data_dir",
            Path("/tmp/jarvis-test-data"),
        ), patch.object(app_main, "init_db"), patch.object(
            app_main.runtime,
            "restore_state",
        ), patch.object(
            app_main.runtime,
            "start_dispatcher",
        ) as start_dispatcher_mock, patch.object(
            app_main.runtime,
            "list_sessions",
            return_value=[],
        ), patch.object(
            app_main.runtime,
            "create_session",
            new=AsyncMock(),
        ) as create_session_mock:
            await app_main.initialize_runtime_for_role()

        start_dispatcher_mock.assert_not_called()
        create_session_mock.assert_awaited_once()

    async def test_worker_role_starts_dispatcher_without_default_session_bootstrap(self) -> None:
        with patch.object(app_main.settings, "jarvis_runtime_role", "worker"), patch.object(
            app_main.settings,
            "data_dir",
            Path("/tmp/jarvis-test-data"),
        ), patch.object(app_main, "init_db"), patch.object(
            app_main.runtime,
            "restore_state",
        ), patch.object(
            app_main.runtime,
            "start_dispatcher",
        ) as start_dispatcher_mock, patch.object(
            app_main.runtime,
            "list_sessions",
            return_value=[],
        ), patch.object(
            app_main.runtime,
            "create_session",
            new=AsyncMock(),
        ) as create_session_mock:
            await app_main.initialize_runtime_for_role()

        start_dispatcher_mock.assert_called_once()
        create_session_mock.assert_not_awaited()
