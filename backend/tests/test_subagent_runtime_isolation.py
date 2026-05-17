from __future__ import annotations

from contextlib import contextmanager
from pathlib import Path
from unittest import IsolatedAsyncioTestCase
from unittest.mock import AsyncMock, patch

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

import app.services.subagent_service as subagent_service
from app.db.base import Base
from app.models import AgentRecord, SessionRecord
from app.runtime.manager import RuntimeManager
from app.schemas.subagents import SubagentRunCreate
from app.services.worktree_service import WorktreeCleanupResult, WorktreeExecutionContext


class SubagentRuntimeIsolationTests(IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.engine = create_engine("sqlite:///:memory:", future=True)
        Base.metadata.create_all(bind=self.engine)
        self.SessionLocal = sessionmaker(bind=self.engine, autoflush=False, autocommit=False, future=True)

        with self._create_session() as db:
            db.add(
                SessionRecord(
                    id="session-1",
                    title="Session",
                    workspace_mode="bound",
                    canonical_workspace_path="/tmp/workspace",
                    workspace_fingerprint="workspace-fp",
                    workspace_label="workspace",
                    status="idle",
                )
            )
            db.commit()

    @contextmanager
    def _create_session(self):
        db = self.SessionLocal()
        try:
            yield db
        finally:
            db.close()

    async def test_run_subagent_worktree_mode_uses_isolated_workspace_and_records_cleanup(self) -> None:
        runtime = RuntimeManager()
        worktree_path = Path("/tmp/worktree-agent-1")
        context = WorktreeExecutionContext(
            base_workspace_path=Path("/tmp/workspace"),
            repo_root=Path("/tmp/workspace"),
            execution_workspace_path=worktree_path,
            branch_name="jarvis-subagent-1-explorer",
            base_revision="abc123",
        )
        cleanup = WorktreeCleanupResult(
            execution_workspace_path=worktree_path,
            branch_name=context.branch_name,
            base_revision=context.base_revision,
            cleanup_status="preserved",
            preserved_reason="dirty_worktree",
        )

        with patch.object(subagent_service, "create_session", self._create_session), patch.object(
            runtime, "_session_workspace", return_value=Path("/tmp/workspace")
        ), patch.object(runtime, "_session_workspace_mode", return_value="bound"), patch.object(
            runtime, "publish", new=AsyncMock()
        ), patch.object(
            runtime,
            "_run_subagent_task",
            new=AsyncMock(return_value="isolated summary"),
        ) as run_mock, patch(
            "app.runtime.manager.worktree_service.prepare_subagent_worktree",
            return_value=context,
        ), patch(
            "app.runtime.manager.worktree_service.finalize_subagent_worktree",
            return_value=cleanup,
        ):
            result = await runtime.run_subagent(
                SubagentRunCreate(
                    session_id="session-1",
                    name="Explorer 1",
                    prompt="Inspect and change files",
                    isolation_mode="worktree",
                )
            )

        run_mock.assert_awaited_once_with("session-1", "Inspect and change files", workspace=worktree_path)
        self.assertEqual(result["subagent"].isolation_mode, "worktree")
        self.assertEqual(result["subagent"].execution_workspace_path, worktree_path.as_posix())
        self.assertEqual(result["subagent"].cleanup_status, "preserved")
        self.assertEqual(result["subagent"].preserved_reason, "dirty_worktree")

        with self._create_session() as db:
            row = db.get(AgentRecord, result["subagent"].id)

        self.assertIsNotNone(row)
        self.assertEqual(row.execution_workspace_path if row else None, worktree_path.as_posix())
        self.assertEqual(row.git_branch if row else None, "jarvis-subagent-1-explorer")
        self.assertEqual(row.cleanup_status if row else None, "preserved")
