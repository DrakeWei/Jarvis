from __future__ import annotations

from contextlib import contextmanager
from unittest import TestCase
from unittest.mock import patch

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

import app.services.approval_service as approval_service
import app.services.tool_service as tool_service
from app.db.base import Base
from app.models import ApprovalRecord, SessionRecord, ToolExecutionRecord


class ServiceListQueryTests(TestCase):
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
            db.add(
                ApprovalRecord(
                    session_id="session-1",
                    approval_type="bash",
                    status="pending",
                    prompt="bash\nls",
                )
            )
            db.add(
                ToolExecutionRecord(
                    session_id="session-1",
                    tool_name="read_file",
                    tool_source="local",
                    status="completed",
                    input_json='{"path":"README.md"}',
                    output_text="ok",
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

    def test_approval_and_tool_list_queries_return_expected_rows(self) -> None:
        with patch.object(approval_service, "create_session", self._create_session), patch.object(
            tool_service,
            "create_session",
            self._create_session,
        ):
            approvals = approval_service.list_approvals("session-1")
            executions = tool_service.list_tool_executions("session-1")

        self.assertEqual([item.approval_type for item in approvals], ["bash"])
        self.assertEqual([item.tool_name for item in executions], ["read_file"])
