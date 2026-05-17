from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from unittest import TestCase
from unittest.mock import patch

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

import app.services.background_job_service as background_job_service
import app.services.ingestion_job_service as ingestion_job_service
import app.services.session_service as session_service
from app.db.base import Base
from app.models import BackgroundJobRecord, EventLogRecord, IngestionJobRecord, SessionRecord
from app.schemas.events import TimelineEvent


class RuntimeHousekeepingTests(TestCase):
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

    def test_purge_expired_ephemeral_events_removes_old_ephemeral_only(self) -> None:
        old_time = datetime.now(timezone.utc) - timedelta(seconds=600)
        with self._create_session() as db:
            db.add(
                EventLogRecord(
                    session_id="session-1",
                    event_type="message.user",
                    content="keep",
                    ephemeral=False,
                    created_at=old_time,
                )
            )
            db.add(
                EventLogRecord(
                    session_id="session-1",
                    event_type="message.assistant.delta",
                    content="drop",
                    ephemeral=True,
                    created_at=old_time,
                )
            )
            db.commit()

        with patch.object(session_service, "create_session", self._create_session), patch(
            "app.services.session_service.settings.jarvis_ephemeral_event_ttl_seconds",
            300,
        ):
            removed = session_service.purge_expired_ephemeral_events()
            remaining = session_service.list_event_records("session-1", include_ephemeral=True)

        self.assertEqual(removed, 1)
        self.assertEqual(len(remaining), 1)
        self.assertEqual(remaining[0].type, "message.user")

    def test_purge_terminal_jobs_removes_old_completed_and_failed_rows(self) -> None:
        now = datetime.now(timezone.utc)
        old_time = now - timedelta(days=10)
        with self._create_session() as db:
            db.add(
                BackgroundJobRecord(
                    session_id="session-1",
                    job_type="turn_execution",
                    command="turn_execution:1",
                    status="completed",
                    completed_at=old_time,
                    updated_at=old_time,
                    created_at=old_time,
                )
            )
            db.add(
                IngestionJobRecord(
                    session_id="session-1",
                    asset_id="asset-1",
                    job_type="asset_ingestion",
                    status="failed",
                    completed_at=old_time,
                    updated_at=old_time,
                    created_at=old_time,
                )
            )
            db.commit()

        with patch.object(background_job_service, "create_session", self._create_session), patch.object(
            ingestion_job_service,
            "create_session",
            self._create_session,
        ), patch(
            "app.services.background_job_service.settings.jarvis_completed_background_job_ttl_seconds",
            7 * 24 * 3600,
        ), patch(
            "app.services.ingestion_job_service.settings.jarvis_completed_ingestion_job_ttl_seconds",
            7 * 24 * 3600,
        ):
            removed_background = background_job_service.purge_terminal_jobs()
            removed_ingestion = ingestion_job_service.purge_terminal_jobs()

        self.assertEqual(removed_background, 1)
        self.assertEqual(removed_ingestion, 1)
