from __future__ import annotations

from unittest import TestCase
from unittest.mock import patch

import app.providers.volcengine_tts_provider as volcengine_tts_provider
from app.providers.base import ProviderRequestError
from app.providers import SpeechSynthesisRequest


def _lp_bytes(value: bytes) -> bytes:
    return len(value).to_bytes(4, "big", signed=False) + value


def _audio_packet(session_id: str, audio: bytes) -> bytes:
    frame = bytearray()
    frame.extend([0x11, volcengine_tts_provider.MSG_AUDIO_ONLY_RESPONSE, 0x10, 0x00])
    frame.extend(int(volcengine_tts_provider.EVENT_TTS_RESPONSE).to_bytes(4, "big", signed=True))
    frame.extend(_lp_bytes(session_id.encode("utf-8")))
    frame.extend(_lp_bytes(audio))
    return bytes(frame)


def _session_finished_packet(session_id: str) -> bytes:
    frame = bytearray()
    frame.extend([0x11, volcengine_tts_provider.MSG_FULL_SERVER_RESPONSE, 0x10, 0x00])
    frame.extend(int(volcengine_tts_provider.EVENT_SESSION_FINISHED).to_bytes(4, "big", signed=True))
    frame.extend(_lp_bytes(session_id.encode("utf-8")))
    frame.extend(_lp_bytes(b"{}"))
    return bytes(frame)


class _FakeWebSocket:
    def __init__(self, responses: list[bytes]) -> None:
        self._responses = list(responses)
        self.sent: list[bytes] = []

    def send(self, data: bytes) -> None:
        self.sent.append(data)

    def recv(self, timeout=None):
        if not self._responses:
            raise RuntimeError("no more responses")
        return self._responses.pop(0)

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False


class VolcengineTTSProviderTests(TestCase):
    def test_build_send_text_packet_encodes_json_payload(self) -> None:
        packet = volcengine_tts_provider._build_send_text_packet({"req_params": {"text": "hello"}})
        self.assertEqual(packet[0], 0x11)
        self.assertEqual(packet[1], volcengine_tts_provider.FRAME_FULL_CLIENT_NO_EVENT)

    def test_parse_audio_only_packet_reads_audio_bytes(self) -> None:
        packet = _audio_packet("session-1", b"\x01\x02")
        parsed = volcengine_tts_provider._parse_server_packet(packet)
        self.assertEqual(parsed.event, volcengine_tts_provider.EVENT_TTS_RESPONSE)
        self.assertEqual(parsed.session_id, "session-1")
        self.assertEqual(parsed.audio, b"\x01\x02")

    def test_synthesize_once_aggregates_audio_chunks(self) -> None:
        fake_ws = _FakeWebSocket([
            _audio_packet("session-1", b"\x00\x01"),
            _audio_packet("session-1", b"\x02\x03"),
            _session_finished_packet("session-1"),
        ])
        with patch.object(volcengine_tts_provider.settings, "jarvis_tts_api_key", "api-key"), patch.object(
            volcengine_tts_provider.settings,
            "jarvis_tts_resource_id",
            "seed-tts-2.0",
        ), patch.object(
            volcengine_tts_provider.settings,
            "jarvis_tts_ws_url",
            "wss://openspeech.bytedance.com/api/v3/tts/unidirectional/stream",
        ), patch(
            "app.providers.volcengine_tts_provider.connect",
            return_value=fake_ws,
        ):
            provider = volcengine_tts_provider.VolcengineTTSProvider()
            result = provider.synthesize_once(
                SpeechSynthesisRequest(text="hello", voice="zh_female_vv_uranus_bigtts", audio_format="mp3")
            )

        self.assertEqual(result.audio_bytes, b"\x00\x01\x02\x03")
        self.assertEqual(result.mime_type, "audio/mpeg")
        self.assertEqual(result.provider_name, provider.provider_name)
        self.assertTrue(fake_ws.sent)
        self.assertEqual(fake_ws.sent[0][1], volcengine_tts_provider.FRAME_FULL_CLIENT_NO_EVENT)
        self.assertEqual(fake_ws.sent[-1][1], volcengine_tts_provider.FRAME_FULL_CLIENT_WITH_EVENT)

    def test_normalize_openai_voice_aliases_to_default_voice(self) -> None:
        normalized = volcengine_tts_provider._normalize_voice_name(
            "alloy",
            default_voice="zh_female_vv_uranus_bigtts",
        )
        self.assertEqual(normalized, "zh_female_vv_uranus_bigtts")

    def test_nonpositive_pitch_is_treated_as_default(self) -> None:
        self.assertIsNone(volcengine_tts_provider._post_process_payload(0))

    def test_collect_audio_retries_with_default_voice_on_resource_mismatch(self) -> None:
        with patch.object(volcengine_tts_provider.settings, "jarvis_tts_api_key", "api-key"), patch.object(
            volcengine_tts_provider.settings,
            "jarvis_tts_resource_id",
            "seed-tts-2.0",
        ), patch.object(
            volcengine_tts_provider.settings,
            "jarvis_tts_ws_url",
            "wss://openspeech.bytedance.com/api/v3/tts/unidirectional/stream",
        ):
            provider = volcengine_tts_provider.VolcengineTTSProvider()
            with patch.object(
                provider,
                "_synthesize_chunks",
                side_effect=[
                    ProviderRequestError("Volcengine TTS returned error event 55000000: {'error': 'resource ID is mismatched with speaker related resource'}"),
                    [b"\x01\x02"],
                ],
            ):
                audio_parts, resolved_voice = provider._collect_audio_with_fallback(
                    SpeechSynthesisRequest(text="hello", voice="custom-incompatible", audio_format="mp3")
                )
        self.assertEqual(audio_parts, [b"\x01\x02"])
        self.assertEqual(resolved_voice, provider._default_voice)
