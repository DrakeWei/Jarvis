from __future__ import annotations

from contextlib import contextmanager
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase
from unittest.mock import patch

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

import app.core.session_assets as session_asset_utils
import app.services.asset_service as asset_service
import app.services.speech_generation_service as speech_generation_service
from app.db.base import Base
from app.models import SessionRecord
from app.providers import GeneratedSpeechResult, SpeechSynthesisRequest


WAV_BYTES = (
    b"RIFF$\x00\x00\x00WAVEfmt "
    b"\x10\x00\x00\x00\x01\x00\x01\x00@\x1f\x00\x00\x80>\x00\x00"
    b"\x02\x00\x10\x00data\x00\x00\x00\x00"
)


class SpeechGenerationServiceTests(TestCase):
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

    def test_generate_speech_persists_generated_audio_asset(self) -> None:
        class FakeProvider:
            def synthesize_once(self, request: SpeechSynthesisRequest) -> GeneratedSpeechResult:
                return GeneratedSpeechResult(
                    audio_bytes=WAV_BYTES,
                    mime_type="audio/wav",
                    provider_name="fake-tts",
                    metadata={"duration_ms": 0, "voice": request.voice or "narrator"},
                )

        with patch.object(asset_service, "create_session", self._create_session), patch.object(
            session_asset_utils.settings,
            "data_dir",
            Path(self.tempdir.name),
        ), patch(
            "app.services.speech_generation_service.create_speech_synthesis_provider",
            return_value=FakeProvider(),
        ):
            result = speech_generation_service.generate_speech(
                "session-1",
                SpeechSynthesisRequest(
                    text="Speak this reply",
                    voice="narrator",
                    audio_format="wav",
                    speed=1.0,
                    pitch=1.0,
                    stream=True,
                ),
            )

        self.assertEqual(result.provider_name, "fake-tts")
        self.assertEqual(result.asset.kind, "generated_audio")
        self.assertEqual(result.asset.origin, "generated")
        self.assertEqual(result.asset.mime_type, "audio/wav")
        self.assertEqual(result.asset.metadata_json.get("provider"), "fake-tts")
        self.assertEqual(result.asset.metadata_json.get("voice"), "narrator")
        self.assertTrue(Path(result.asset.storage_path).exists())

    def test_generate_speech_rejects_empty_text(self) -> None:
        with self.assertRaises(speech_generation_service.SpeechGenerationError):
            speech_generation_service.generate_speech(
                "session-1",
                SpeechSynthesisRequest(text="   "),
            )
