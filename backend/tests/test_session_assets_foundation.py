from __future__ import annotations

from contextlib import contextmanager
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase
from unittest.mock import patch

from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

import app.core.session_assets as session_asset_utils
import app.services.asset_service as asset_service
import app.services.session_service as session_service
from app.db.base import Base
from app.models import MessageAssetRecord, MessageRecord, SessionRecord
from app.schemas.events import MessageCreate


class SessionAssetFoundationTests(TestCase):
    def setUp(self) -> None:
        super().setUp()
        self.tempdir = TemporaryDirectory()
        self.engine = create_engine("sqlite:///:memory:", future=True)
        Base.metadata.create_all(bind=self.engine)
        self.SessionLocal = sessionmaker(bind=self.engine, autoflush=False, autocommit=False, future=True)
        self.session_id = "session-1"

        with self._create_session() as db:
            db.add(
                SessionRecord(
                    id=self.session_id,
                    title="Test Session",
                    workspace_mode="bound",
                    canonical_workspace_path="/tmp/workspace",
                    workspace_fingerprint="workspace-fp",
                    workspace_label="workspace",
                    status="idle",
                )
            )
            db.commit()

    def tearDown(self) -> None:
        self.tempdir.cleanup()
        super().tearDown()

    @contextmanager
    def _create_session(self):
        db = self.SessionLocal()
        try:
            yield db
        finally:
            db.close()

    def test_sanitize_filename_removes_path_segments_and_spaces(self) -> None:
        self.assertEqual(session_asset_utils.sanitize_filename("../Quarterly Report?.pdf"), "Quarterly_Report_.pdf")

    def test_create_asset_record_allocates_session_scoped_storage_path(self) -> None:
        with patch.object(asset_service, "create_session", self._create_session), patch.object(
            session_asset_utils.settings,
            "data_dir",
            Path(self.tempdir.name),
        ):
            asset = asset_service.create_asset_record(
                self.session_id,
                kind="pdf",
                mime_type="application/pdf",
                filename="Quarterly Report?.pdf",
                size_bytes=2048,
            )

        expected_suffix = Path("sessions") / self.session_id / "assets" / asset.id / "original" / "Quarterly_Report_.pdf"
        self.assertEqual(Path(asset.storage_path), Path(self.tempdir.name) / expected_suffix)
        self.assertEqual(asset.status, "uploaded")
        self.assertEqual(asset.size_bytes, 2048)

    def test_create_asset_record_preserves_unicode_display_filename(self) -> None:
        with patch.object(asset_service, "create_session", self._create_session), patch.object(
            session_asset_utils.settings,
            "data_dir",
            Path(self.tempdir.name),
        ):
            asset = asset_service.create_asset_record(
                self.session_id,
                kind="pdf",
                mime_type="application/pdf",
                filename="中文计划?.pdf",
                size_bytes=1024,
            )

        self.assertEqual(asset.filename, "中文计划?.pdf")
        self.assertIn("中文计划_.pdf", asset.storage_path)

    def test_create_message_record_links_assets_atomically(self) -> None:
        with patch.object(asset_service, "create_session", self._create_session), patch.object(
            session_service,
            "create_session",
            self._create_session,
        ), patch.object(
            session_asset_utils.settings,
            "data_dir",
            Path(self.tempdir.name),
        ):
            asset = asset_service.create_asset_record(
                self.session_id,
                kind="image",
                mime_type="image/png",
                filename="screen.png",
                size_bytes=512,
            )
            message = session_service.create_message_record(
                self.session_id,
                MessageCreate(role="user", content="Inspect this image", asset_ids=[asset.id]),
            )

        with self._create_session() as db:
            linked = db.scalars(
                select(MessageAssetRecord.asset_id).where(MessageAssetRecord.message_id == message.id)
            ).all()
        self.assertEqual(linked, [asset.id])

    def test_create_message_record_rejects_unknown_asset_without_persisting_message(self) -> None:
        with patch.object(asset_service, "create_session", self._create_session), patch.object(
            session_service,
            "create_session",
            self._create_session,
        ):
            with self.assertRaises(ValueError):
                session_service.create_message_record(
                    self.session_id,
                    MessageCreate(role="user", content="Use missing asset", asset_ids=["asset-missing"]),
                )

        with self._create_session() as db:
            persisted_messages = db.scalars(select(MessageRecord)).all()
        self.assertEqual(persisted_messages, [])

    def test_list_message_records_preserves_asset_references_for_replay(self) -> None:
        with patch.object(asset_service, "create_session", self._create_session), patch.object(
            session_service,
            "create_session",
            self._create_session,
        ), patch.object(
            session_asset_utils.settings,
            "data_dir",
            Path(self.tempdir.name),
        ):
            asset = asset_service.create_asset_record(
                self.session_id,
                kind="pdf",
                mime_type="application/pdf",
                filename="report.pdf",
                size_bytes=2048,
            )
            session_service.create_message_record(
                self.session_id,
                MessageCreate(role="user", content="Review the report", asset_ids=[asset.id]),
            )
            transcript = session_service.list_message_records(self.session_id)

        self.assertEqual(transcript[0]["role"], "user")
        parts = transcript[0]["content"]
        self.assertTrue(isinstance(parts, list))
        self.assertTrue(any(isinstance(part, dict) and part.get("type") == "asset_ref" and part.get("asset_id") == asset.id for part in parts))

    def test_list_sessions_orders_by_latest_message_activity_in_sql(self) -> None:
        with self._create_session() as db:
            db.add(
                SessionRecord(
                    id="session-2",
                    title="Later Session",
                    workspace_mode="bound",
                    canonical_workspace_path="/tmp/workspace-2",
                    workspace_fingerprint="workspace-fp-2",
                    workspace_label="workspace-2",
                    status="idle",
                )
            )
            db.commit()

        with patch.object(session_service, "create_session", self._create_session):
            session_service.create_message_record(
                "session-1",
                MessageCreate(role="user", content="older message"),
            )
            session_service.create_message_record(
                "session-2",
                MessageCreate(role="user", content="newer message"),
            )
            sessions = session_service.list_sessions()

        self.assertEqual([session.session_id for session in sessions[:2]], ["session-2", "session-1"])
