from __future__ import annotations

from contextlib import contextmanager
from io import BytesIO
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase
from unittest.mock import patch

from docx import Document
from PIL import Image
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

import app.core.session_assets as session_asset_utils
import app.services.asset_ingestion_service as asset_ingestion_service
import app.services.asset_service as asset_service
from app.db.base import Base
from app.models import SessionRecord


class AssetIngestionServiceTests(TestCase):
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

    def test_validate_upload_rejects_unsupported_type(self) -> None:
        with self.assertRaises(asset_ingestion_service.AssetUploadError):
            asset_ingestion_service.validate_upload("notes.zip", "application/zip", 128)

    def test_image_ingestion_creates_preview_and_marks_ready(self) -> None:
        image = Image.new("RGB", (640, 480), color=(120, 80, 10))
        image_bytes = BytesIO()
        image.save(image_bytes, format="PNG")

        with patch.object(asset_service, "create_session", self._create_session), patch.object(
            session_asset_utils.settings,
            "data_dir",
            Path(self.tempdir.name),
        ):
            asset = asset_ingestion_service.stage_uploaded_asset(
                self.session_id,
                "screen.png",
                "image/png",
                image_bytes.getvalue(),
            )
            ingested = asset_ingestion_service.ingest_asset(asset.id)

        self.assertEqual(ingested.status, "ready")
        self.assertTrue(ingested.preview_path)
        self.assertTrue(Path(ingested.preview_path).exists())

    def test_docx_ingestion_creates_chunks(self) -> None:
        document = Document()
        document.add_heading("Status", level=1)
        document.add_paragraph("The runtime is now asset aware.")
        document.add_paragraph("The composer will eventually support drag and drop.")
        docx_path = Path(self.tempdir.name) / "sample.docx"
        document.save(docx_path)

        with patch.object(asset_service, "create_session", self._create_session), patch.object(
            session_asset_utils.settings,
            "data_dir",
            Path(self.tempdir.name),
        ):
            asset = asset_ingestion_service.stage_uploaded_asset(
                self.session_id,
                "sample.docx",
                "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                docx_path.read_bytes(),
            )
            ingested = asset_ingestion_service.ingest_asset(asset.id)
            chunks = asset_service.list_asset_chunks(asset.id)

        self.assertEqual(ingested.status, "ready")
        self.assertGreaterEqual(len(chunks), 1)
        self.assertTrue(any("asset aware" in chunk.content for chunk in chunks))
