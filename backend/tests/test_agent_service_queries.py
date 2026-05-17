from __future__ import annotations

from contextlib import contextmanager
from unittest import TestCase
from unittest.mock import patch

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

import app.services.subagent_service as subagent_service
import app.services.teammate_service as teammate_service
from app.db.base import Base
from app.models import AgentMessageRecord, AgentRecord, SessionRecord


class AgentServiceQueryTests(TestCase):
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
                AgentRecord(
                    id=1,
                    session_id="session-1",
                    name="Scout 1",
                    role="Scout",
                    kind="teammate",
                    status="idle",
                )
            )
            db.add(
                AgentRecord(
                    id=2,
                    session_id="session-1",
                    name="Explorer 1",
                    role="Explorer",
                    kind="subagent",
                    status="running",
                )
            )
            db.add(
                AgentMessageRecord(
                    agent_id=2,
                    direction="summary",
                    content="Summary text",
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

    def test_teammate_queries_return_expected_rows(self) -> None:
        with patch.object(teammate_service, "create_session", self._create_session):
            teammates = teammate_service.list_teammates("session-1")
            messages = teammate_service.list_teammate_messages(1)

        self.assertEqual([item.name for item in teammates], ["Scout 1"])
        self.assertEqual(messages, [])

    def test_subagent_queries_return_expected_rows(self) -> None:
        with patch.object(subagent_service, "create_session", self._create_session):
            subagents = subagent_service.list_subagents("session-1")
            summary = subagent_service.get_subagent_summary(2)

        self.assertEqual([item.name for item in subagents], ["Explorer 1"])
        self.assertEqual(summary, "Summary text")
