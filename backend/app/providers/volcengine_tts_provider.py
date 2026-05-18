from __future__ import annotations

import asyncio
import json
import ssl
from dataclasses import dataclass
from typing import AsyncIterator, Iterable
from uuid import uuid4

from websockets.sync.client import connect

from app.core.config import settings
from app.providers.base import ProviderConfigError, ProviderRequestError
from app.providers.capabilities import AudioChunkEvent, GeneratedSpeechResult, SpeechSynthesisRequest


EVENT_CONNECTION_FINISHED = 52
EVENT_SESSION_FINISHED = 152
EVENT_SESSION_FAILED = 153
EVENT_TTS_RESPONSE = 352

FRAME_FULL_CLIENT_NO_EVENT = 0x10
FRAME_FULL_CLIENT_WITH_EVENT = 0x14

MSG_FULL_SERVER_RESPONSE = 0x94
MSG_AUDIO_ONLY_RESPONSE = 0xB4
MSG_ERROR = 0xF0

SERIAL_JSON = 0x10
EVENT_FINISH_CONNECTION = 2


@dataclass(frozen=True)
class _ParsedServerPacket:
    msg_type: int
    event: int
    session_id: str = ""
    connection_id: str = ""
    payload: object | None = None
    audio: bytes = b""
    error_code: int = 0


class VolcengineTTSProvider:
    provider_name = "volcengine_tts_v3_unidirectional"

    def __init__(self) -> None:
        self._api_key = settings.jarvis_tts_api_key.strip()
        self._resource_id = settings.jarvis_tts_resource_id.strip()
        self._ws_url = settings.jarvis_tts_ws_url.strip()
        self._default_voice = settings.jarvis_tts_default_voice.strip()
        self._sample_rate = max(8000, int(settings.jarvis_tts_default_sample_rate))
        self._bit_rate = max(32, int(settings.jarvis_tts_default_bit_rate))
        self._connect_timeout = max(1.0, float(settings.jarvis_tts_connect_timeout_seconds))
        self._session_timeout = max(1.0, float(settings.jarvis_tts_session_timeout_seconds))
        self._user_uid = settings.jarvis_tts_user_uid.strip() or "jarvis"
        if not self._api_key:
            raise ProviderConfigError("Volcengine TTS is not configured: missing JARVIS_TTS_API_KEY.")
        if not self._resource_id:
            raise ProviderConfigError("Volcengine TTS is not configured: missing JARVIS_TTS_RESOURCE_ID.")
        if not self._ws_url:
            raise ProviderConfigError("Volcengine TTS is not configured: missing JARVIS_TTS_WS_URL.")

    async def synthesize_stream(self, request: SpeechSynthesisRequest) -> AsyncIterator[AudioChunkEvent]:
        audio_parts, resolved_voice = self._collect_audio_with_fallback(request)
        for index, chunk in enumerate(audio_parts, start=1):
            yield AudioChunkEvent(
                sequence=index,
                audio_bytes=chunk,
                audio_format=request.audio_format,
                is_final=False,
                metadata={"provider": self.provider_name, "voice": resolved_voice},
            )
            await asyncio.sleep(0)

    def synthesize_once(self, request: SpeechSynthesisRequest) -> GeneratedSpeechResult:
        audio_parts, resolved_voice = self._collect_audio_with_fallback(request)
        if not audio_parts:
            raise ProviderRequestError("Volcengine TTS returned no audio chunks.")
        mime_type = _audio_mime_type(request.audio_format)
        return GeneratedSpeechResult(
            audio_bytes=b"".join(audio_parts),
            mime_type=mime_type,
            provider_name=self.provider_name,
            metadata={
                "format": request.audio_format,
                "sample_rate": self._sample_rate,
                "resource_id": self._resource_id,
                "voice": resolved_voice,
                "requested_voice": request.voice,
            },
        )

    def _collect_audio_with_fallback(self, request: SpeechSynthesisRequest) -> tuple[list[bytes], str]:
        normalized_request = _normalized_request(request, default_voice=self._default_voice)
        resolved_voice = normalized_request.voice or self._default_voice
        try:
            return list(self._synthesize_chunks(normalized_request)), resolved_voice
        except ProviderRequestError as exc:
            if not _should_retry_with_default_voice(exc, resolved_voice, self._default_voice):
                raise
            fallback_request = SpeechSynthesisRequest(
                text=request.text,
                voice=self._default_voice,
                audio_format=request.audio_format,
                speed=request.speed,
                pitch=request.pitch,
                stream=request.stream,
            )
            return list(self._synthesize_chunks(fallback_request)), self._default_voice

    def _synthesize_chunks(self, request: SpeechSynthesisRequest) -> Iterable[bytes]:
        payload = {
            "user": {"uid": self._user_uid},
            "req_params": _build_req_params(
                request,
                default_voice=self._default_voice,
                sample_rate=self._sample_rate,
                bit_rate=self._bit_rate,
            ),
        }
        headers = {
            "X-Api-Key": self._api_key,
            "X-Api-Resource-Id": self._resource_id,
            "X-Api-Request-Id": str(uuid4()),
            "X-Control-Require-Usage-Tokens-Return": "*",
        }
        try:
            with connect(
                self._ws_url,
                ssl=_build_ssl_context(),
                additional_headers=headers,
                open_timeout=self._connect_timeout,
                close_timeout=self._connect_timeout,
                compression=None,
                max_size=None,
            ) as websocket:
                websocket.send(_build_send_text_packet(payload))
                while True:
                    raw = websocket.recv(timeout=self._session_timeout)
                    if isinstance(raw, str):
                        raw = raw.encode("utf-8")
                    packet = _parse_server_packet(raw)
                    if packet.msg_type == MSG_ERROR:
                        raise ProviderRequestError(
                            f"Volcengine TTS returned error event {packet.error_code}: {packet.payload}"
                        )
                    if packet.event == EVENT_SESSION_FAILED:
                        raise ProviderRequestError(f"Volcengine TTS session failed: {packet.payload}")
                    if packet.msg_type == MSG_AUDIO_ONLY_RESPONSE and packet.audio:
                        yield packet.audio
                    if packet.event == EVENT_SESSION_FINISHED:
                        websocket.send(_build_finish_connection_packet())
                        break
        except ProviderRequestError:
            raise
        except Exception as exc:
            raise ProviderRequestError(f"Volcengine TTS request failed: {exc}") from exc


def _build_req_params(
    request: SpeechSynthesisRequest,
    *,
    default_voice: str,
    sample_rate: int,
    bit_rate: int,
) -> dict[str, object]:
    audio_format = (request.audio_format or "mp3").strip().lower() or "mp3"
    voice = (_normalized_request(request, default_voice=default_voice).voice or default_voice).strip() or default_voice
    req_params: dict[str, object] = {
        "speaker": voice,
        "text": request.text,
        "audio_params": {
            "format": audio_format,
            "sample_rate": sample_rate,
        },
    }
    if audio_format == "mp3":
        req_params["audio_params"]["bit_rate"] = bit_rate
    speech_rate = _speed_to_speech_rate(request.speed)
    if speech_rate:
        req_params["audio_params"]["speech_rate"] = speech_rate
    post_process = _post_process_payload(request.pitch)
    if post_process:
        req_params["additions"] = json.dumps({"post_process": post_process}, ensure_ascii=False)
    return req_params


def _speed_to_speech_rate(speed: float) -> int:
    normalized = _normalize_ratio(speed)
    if abs(normalized - 1.0) < 1e-6:
        return 0
    scaled = round((normalized - 1.0) * 100)
    return max(-50, min(100, scaled))


def _post_process_payload(pitch: float) -> dict[str, int] | None:
    normalized = _normalize_ratio(pitch)
    if abs(normalized - 1.0) < 1e-6:
        return None
    semitones = round((normalized - 1.0) * 12)
    return {"pitch": max(-12, min(12, semitones))}


def _normalized_request(request: SpeechSynthesisRequest, *, default_voice: str) -> SpeechSynthesisRequest:
    resolved_voice = _normalize_voice_name(request.voice, default_voice=default_voice)
    return SpeechSynthesisRequest(
        text=request.text,
        voice=resolved_voice,
        audio_format=request.audio_format,
        speed=_normalize_ratio(request.speed),
        pitch=_normalize_ratio(request.pitch),
        stream=request.stream,
    )


def _normalize_voice_name(voice: str | None, *, default_voice: str) -> str:
    raw = str(voice or "").strip()
    if not raw:
        return default_voice
    if raw.lower() in {
        "alloy",
        "ash",
        "ballad",
        "coral",
        "echo",
        "fable",
        "nova",
        "onyx",
        "sage",
        "shimmer",
        "verse",
    }:
        return default_voice
    return raw


def _normalize_ratio(value: float | None) -> float:
    if value is None:
        return 1.0
    try:
        numeric = float(value)
    except Exception:
        return 1.0
    if numeric <= 0:
        return 1.0
    return max(0.5, min(2.0, numeric))


def _should_retry_with_default_voice(exc: ProviderRequestError, resolved_voice: str, default_voice: str) -> bool:
    if not resolved_voice or resolved_voice == default_voice:
        return False
    message = str(exc).lower()
    return "resource id is mismatched with speaker related resource" in message


def _build_send_text_packet(payload: dict[str, object]) -> bytes:
    payload_bytes = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    header = bytearray()
    header.extend([0x11, FRAME_FULL_CLIENT_NO_EVENT, SERIAL_JSON, 0x00])
    header.extend(len(payload_bytes).to_bytes(4, "big", signed=False))
    header.extend(payload_bytes)
    return bytes(header)


def _build_finish_connection_packet() -> bytes:
    payload_bytes = b"{}"
    header = bytearray()
    header.extend([0x11, FRAME_FULL_CLIENT_WITH_EVENT, SERIAL_JSON, 0x00])
    header.extend(int(EVENT_FINISH_CONNECTION).to_bytes(4, "big", signed=True))
    header.extend(len(payload_bytes).to_bytes(4, "big", signed=False))
    header.extend(payload_bytes)
    return bytes(header)


def _parse_server_packet(data: bytes) -> _ParsedServerPacket:
    if len(data) < 4:
        return _ParsedServerPacket(msg_type=0, event=0)
    msg_type = data[1]
    cursor = 4
    if msg_type == MSG_ERROR:
        error_code, cursor = _read_i32(data, cursor)
        payload, _ = _read_lp_payload(data, cursor)
        return _ParsedServerPacket(msg_type=msg_type, event=error_code, error_code=error_code, payload=payload)

    event, cursor = _read_i32(data, cursor)
    if event == EVENT_CONNECTION_FINISHED:
        connection_id, cursor = _read_lp_text(data, cursor)
        payload, _ = _read_lp_payload(data, cursor)
        return _ParsedServerPacket(msg_type=msg_type, event=event, connection_id=connection_id, payload=payload)

    session_id, cursor = _read_lp_text(data, cursor)
    if msg_type == MSG_AUDIO_ONLY_RESPONSE:
        audio, _ = _read_lp_bytes(data, cursor)
        return _ParsedServerPacket(msg_type=msg_type, event=event, session_id=session_id, audio=audio)

    payload, _ = _read_lp_payload(data, cursor)
    return _ParsedServerPacket(msg_type=msg_type, event=event, session_id=session_id, payload=payload)


def _read_i32(data: bytes, cursor: int) -> tuple[int, int]:
    end = cursor + 4
    if end > len(data):
        return 0, len(data)
    return int.from_bytes(data[cursor:end], "big", signed=True), end


def _read_u32(data: bytes, cursor: int) -> tuple[int, int]:
    end = cursor + 4
    if end > len(data):
        return 0, len(data)
    return int.from_bytes(data[cursor:end], "big", signed=False), end


def _read_lp_bytes(data: bytes, cursor: int) -> tuple[bytes, int]:
    size, cursor = _read_u32(data, cursor)
    end = cursor + size
    if end > len(data):
        return b"", len(data)
    return data[cursor:end], end


def _read_lp_text(data: bytes, cursor: int) -> tuple[str, int]:
    chunk, cursor = _read_lp_bytes(data, cursor)
    return chunk.decode("utf-8", errors="replace"), cursor


def _read_lp_payload(data: bytes, cursor: int) -> tuple[object | None, int]:
    chunk, cursor = _read_lp_bytes(data, cursor)
    if not chunk:
        return None, cursor
    text = chunk.decode("utf-8", errors="replace")
    try:
        return json.loads(text), cursor
    except Exception:
        return text, cursor


def _audio_mime_type(audio_format: str) -> str:
    normalized = str(audio_format or "mp3").strip().lower()
    if normalized == "wav":
        return "audio/wav"
    if normalized == "ogg_opus":
        return "audio/ogg"
    if normalized == "pcm":
        return "audio/L16"
    return "audio/mpeg"


def _build_ssl_context() -> ssl.SSLContext:
    cafile = None
    try:
        import os

        cafile = os.getenv("JARVIS_TTS_CA_BUNDLE") or os.getenv("SSL_CERT_FILE") or os.getenv("OPENAI_CA_BUNDLE")
    except Exception:
        cafile = None
    if not cafile:
        try:
            import certifi

            cafile = certifi.where()
        except ImportError:
            cafile = None
    return ssl.create_default_context(cafile=cafile)
