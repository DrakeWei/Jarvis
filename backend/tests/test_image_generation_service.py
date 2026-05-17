from __future__ import annotations

import base64
import json
from contextlib import contextmanager
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase
from unittest.mock import patch

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

import app.core.session_assets as session_asset_utils
import app.services.asset_service as asset_service
import app.services.image_generation_service as image_generation_service
from app.db.base import Base
from app.models import SessionRecord


PNG_1X1_BASE64 = (
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO5Xn7sAAAAASUVORK5CYII="
)


class ImageGenerationServiceTests(TestCase):
    def setUp(self) -> None:
        super().setUp()
        self.tempdir = TemporaryDirectory()
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

    def tearDown(self) -> None:
        try:
            self.engine.dispose()
        finally:
            self.tempdir.cleanup()
        super().tearDown()

    @contextmanager
    def _create_session(self):
        db = self.SessionLocal()
        try:
            yield db
        finally:
            db.close()

    def test_generate_image_persists_asset_and_preview(self) -> None:
        payload = {
            "data": [
                {
                    "b64_json": PNG_1X1_BASE64,
                    "revised_prompt": "A bright orange cat portrait",
                }
            ]
        }

        class FakeResponse:
            def __enter__(self):
                return self

            def __exit__(self, exc_type, exc, tb):
                return False

            def read(self) -> bytes:
                return json.dumps(payload).encode("utf-8")

        with patch.object(asset_service, "create_session", self._create_session), patch.object(
            session_asset_utils.settings,
            "data_dir",
            Path(self.tempdir.name),
        ), patch(
            "app.services.image_generation_service.urllib.request.urlopen",
            return_value=FakeResponse(),
        ):
            result = image_generation_service.generate_image(
                "session-1",
                "draw an orange cat",
                size="1024x1024",
            )

        self.assertEqual(result.model, image_generation_service.settings.jarvis_image_model)
        self.assertEqual(result.revised_prompt, "A bright orange cat portrait")
        self.assertTrue(Path(result.asset.storage_path).exists())
        self.assertTrue(result.asset.preview_path)
        self.assertTrue(Path(result.asset.preview_path or "").exists())
        self.assertEqual(result.asset.kind, "image")
        self.assertEqual(result.asset.status, "ready")

    def test_generate_image_with_asset_ids_uses_edit_endpoint(self) -> None:
        payload = {
            "data": [
                {
                    "b64_json": PNG_1X1_BASE64,
                }
            ]
        }
        seen: dict[str, object] = {}

        class FakeResponse:
            def __enter__(self):
                return self

            def __exit__(self, exc_type, exc, tb):
                return False

            def read(self) -> bytes:
                return json.dumps(payload).encode("utf-8")

        def fake_urlopen(request, timeout=None, context=None):
            seen["url"] = request.full_url
            seen["body"] = json.loads(request.data.decode("utf-8"))
            return FakeResponse()

        source_bytes = base64.b64decode(PNG_1X1_BASE64)

        with patch.object(asset_service, "create_session", self._create_session), patch.object(
            session_asset_utils.settings,
            "data_dir",
            Path(self.tempdir.name),
        ), patch(
            "app.services.image_generation_service.urllib.request.urlopen",
            side_effect=fake_urlopen,
        ):
            source_asset = asset_service.create_asset_record(
                "session-1",
                kind="image",
                mime_type="image/png",
                filename="source.png",
                size_bytes=len(source_bytes),
                status="ready",
            )
            Path(source_asset.storage_path).parent.mkdir(parents=True, exist_ok=True)
            Path(source_asset.storage_path).write_bytes(source_bytes)

            result = image_generation_service.generate_image(
                "session-1",
                "make the cat wear sunglasses",
                asset_ids=[source_asset.id],
            )

        self.assertIn("/images/edits", str(seen.get("url")))
        body = seen.get("body") if isinstance(seen.get("body"), dict) else {}
        images = body.get("images", []) if isinstance(body, dict) else []
        self.assertTrue(images)
        self.assertTrue(str(images[0].get("image_url", "")).startswith("data:image/png;base64,"))
        self.assertEqual(result.asset.kind, "image")

    def test_generate_image_with_mask_asset_id_includes_mask(self) -> None:
        payload = {"data": [{"b64_json": PNG_1X1_BASE64}]}
        seen: dict[str, object] = {}

        class FakeResponse:
            def __enter__(self):
                return self

            def __exit__(self, exc_type, exc, tb):
                return False

            def read(self) -> bytes:
                return json.dumps(payload).encode("utf-8")

        def fake_urlopen(request, timeout=None, context=None):
            seen["body"] = json.loads(request.data.decode("utf-8"))
            return FakeResponse()

        source_bytes = base64.b64decode(PNG_1X1_BASE64)

        with patch.object(asset_service, "create_session", self._create_session), patch.object(
            session_asset_utils.settings,
            "data_dir",
            Path(self.tempdir.name),
        ), patch(
            "app.services.image_generation_service.urllib.request.urlopen",
            side_effect=fake_urlopen,
        ):
            source_asset = asset_service.create_asset_record(
                "session-1",
                kind="image",
                mime_type="image/png",
                filename="source.png",
                size_bytes=len(source_bytes),
                status="ready",
            )
            mask_asset = asset_service.create_asset_record(
                "session-1",
                kind="image",
                mime_type="image/png",
                filename="mask.png",
                size_bytes=len(source_bytes),
                status="ready",
            )
            Path(source_asset.storage_path).parent.mkdir(parents=True, exist_ok=True)
            Path(source_asset.storage_path).write_bytes(source_bytes)
            Path(mask_asset.storage_path).parent.mkdir(parents=True, exist_ok=True)
            Path(mask_asset.storage_path).write_bytes(source_bytes)

            image_generation_service.generate_image(
                "session-1",
                "replace the background",
                asset_ids=[source_asset.id],
                mask_asset_id=mask_asset.id,
                input_fidelity="high",
            )

        body = seen.get("body") if isinstance(seen.get("body"), dict) else {}
        self.assertEqual(body.get("input_fidelity"), "high")
        self.assertTrue(str(body.get("mask", {}).get("image_url", "")).startswith("data:image/png;base64,"))

    def test_generate_image_rejects_non_png_mask_asset(self) -> None:
        source_bytes = base64.b64decode(PNG_1X1_BASE64)

        with patch.object(asset_service, "create_session", self._create_session), patch.object(
            session_asset_utils.settings,
            "data_dir",
            Path(self.tempdir.name),
        ):
            source_asset = asset_service.create_asset_record(
                "session-1",
                kind="image",
                mime_type="image/png",
                filename="source.png",
                size_bytes=len(source_bytes),
                status="ready",
            )
            mask_asset = asset_service.create_asset_record(
                "session-1",
                kind="image",
                mime_type="image/jpeg",
                filename="mask.jpg",
                size_bytes=len(source_bytes),
                status="ready",
            )
            Path(source_asset.storage_path).parent.mkdir(parents=True, exist_ok=True)
            Path(source_asset.storage_path).write_bytes(source_bytes)
            Path(mask_asset.storage_path).parent.mkdir(parents=True, exist_ok=True)
            Path(mask_asset.storage_path).write_bytes(source_bytes)

            with self.assertRaises(image_generation_service.ImageGenerationError):
                image_generation_service.generate_image(
                    "session-1",
                    "replace the background",
                    asset_ids=[source_asset.id],
                    mask_asset_id=mask_asset.id,
                )

    def test_generate_image_falls_back_to_official_openai_images_endpoint_for_custom_provider(self) -> None:
        payload = {"data": [{"b64_json": PNG_1X1_BASE64}]}
        seen: dict[str, object] = {}

        class FakeResponse:
            def __enter__(self):
                return self

            def __exit__(self, exc_type, exc, tb):
                return False

            def read(self) -> bytes:
                return json.dumps(payload).encode("utf-8")

        def fake_urlopen(request, timeout=None, context=None):
            seen["url"] = request.full_url
            return FakeResponse()

        with patch.object(asset_service, "create_session", self._create_session), patch.object(
            session_asset_utils.settings,
            "data_dir",
            Path(self.tempdir.name),
        ), patch.object(
            image_generation_service.settings,
            "openai_base_url",
            "https://genai-sg-og.tiktok-row.org/gpt/openapi/online",
        ), patch.object(
            image_generation_service.settings,
            "openai_query_params",
            {"api-version": "2024-03-01-preview"},
        ), patch.object(
            image_generation_service.settings,
            "jarvis_image_base_url",
            "",
        ), patch.object(
            image_generation_service.settings,
            "jarvis_image_query_params",
            {},
        ), patch(
            "app.services.image_generation_service.urllib.request.urlopen",
            side_effect=fake_urlopen,
        ):
            image_generation_service.generate_image("session-1", "draw an orange cat")

        self.assertTrue(str(seen.get("url")).startswith("https://api.openai.com/v1/images/generations"))
        self.assertNotIn("api-version=", str(seen.get("url")))

    def test_generate_image_uses_explicit_image_provider_query_params_when_configured(self) -> None:
        payload = {"data": [{"b64_json": PNG_1X1_BASE64}]}
        seen: dict[str, object] = {}

        class FakeResponse:
            def __enter__(self):
                return self

            def __exit__(self, exc_type, exc, tb):
                return False

            def read(self) -> bytes:
                return json.dumps(payload).encode("utf-8")

        def fake_urlopen(request, timeout=None, context=None):
            seen["url"] = request.full_url
            return FakeResponse()

        with patch.object(asset_service, "create_session", self._create_session), patch.object(
            session_asset_utils.settings,
            "data_dir",
            Path(self.tempdir.name),
        ), patch.object(
            image_generation_service.settings,
            "jarvis_image_base_url",
            "https://example.com/openai",
        ), patch.object(
            image_generation_service.settings,
            "jarvis_image_query_params",
            {"api-version": "2024-03-01-preview"},
        ), patch(
            "app.services.image_generation_service.urllib.request.urlopen",
            side_effect=fake_urlopen,
        ):
            image_generation_service.generate_image("session-1", "draw an orange cat")

        self.assertEqual(
            str(seen.get("url")),
            "https://example.com/openai/images/generations?api-version=2024-03-01-preview",
        )
