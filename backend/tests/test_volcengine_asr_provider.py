from __future__ import annotations

import json
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase
from unittest.mock import patch

import app.providers.volcengine_asr_provider as volcengine_asr_provider
from app.providers import SpeechRecognitionRequest


class _FakeResponse:
    def __init__(self, payload: dict[str, object], headers: dict[str, str] | None = None) -> None:
        self._payload = payload
        self.headers = headers or {"X-Api-Status-Code": "20000000"}

    def read(self) -> bytes:
        return json.dumps(self._payload).encode("utf-8")

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False


class VolcengineASRProviderTests(TestCase):
    def test_request_headers_fall_back_to_app_and_access_key(self) -> None:
        headers = volcengine_asr_provider._request_headers(
            "req-1",
            "volc.bigasr.auc_turbo",
            "",
            "app-key",
            "access-key",
        )
        self.assertEqual(headers["X-Api-App-Key"], "app-key")
        self.assertEqual(headers["X-Api-Access-Key"], "access-key")
        self.assertEqual(headers["X-Api-Sequence"], "-1")

    def test_transcribe_parses_result_and_segments(self) -> None:
        payload = {
            "audio_info": {"duration": 1234},
            "result": {
                "text": "hello world",
                "utterances": [
                    {"text": "hello", "start_time": 0, "end_time": 500},
                    {"text": "world", "start_time": 600, "end_time": 1200},
                ],
            },
        }
        with TemporaryDirectory() as tempdir:
            audio_path = Path(tempdir) / "sample.wav"
            audio_path.write_bytes(b"RIFFxxxxWAVE")
            with patch.object(volcengine_asr_provider.settings, "jarvis_asr_api_base_url", "https://openspeech.bytedance.com/api/v3/auc/bigmodel/recognize/flash"), patch.object(
                volcengine_asr_provider.settings,
                "jarvis_asr_resource_id",
                "volc.bigasr.auc_turbo",
            ), patch.object(
                volcengine_asr_provider.settings,
                "jarvis_asr_api_key",
                "api-key",
            ), patch.object(
                volcengine_asr_provider.settings,
                "jarvis_asr_app_key",
                "",
            ), patch.object(
                volcengine_asr_provider.settings,
                "jarvis_asr_access_key",
                "",
            ), patch(
                "app.providers.volcengine_asr_provider.urllib.request.urlopen",
                return_value=_FakeResponse(payload),
            ):
                provider = volcengine_asr_provider.VolcengineASRProvider()
                result = provider.transcribe(
                    SpeechRecognitionRequest(
                        asset_id="asset-1",
                        mime_type="audio/wav",
                        path=audio_path.as_posix(),
                    )
                )

        self.assertEqual(result.text, "hello world")
        self.assertEqual(len(result.segments), 2)
        self.assertEqual(result.segments[0].text, "hello")
        self.assertEqual(result.segments[1].end_ms, 1200)
        self.assertEqual(result.metadata["duration_ms"], 1234)
