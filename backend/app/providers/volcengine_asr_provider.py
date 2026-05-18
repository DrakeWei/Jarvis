from __future__ import annotations

import base64
import json
import ssl
import urllib.error
import urllib.request
from pathlib import Path
from uuid import uuid4

from app.core.config import settings
from app.providers.base import ProviderConfigError, ProviderRequestError
from app.providers.capabilities import SpeechRecognitionRequest, TranscriptResult, TranscriptSegment


_SUPPORTED_FORMATS = {
    ".wav": "wav",
    ".mp3": "mp3",
    ".ogg": "ogg_opus",
}


class VolcengineASRProvider:
    provider_name = "volcengine_asr_flash"

    def __init__(self) -> None:
        self._api_base_url = settings.jarvis_asr_api_base_url.strip()
        self._resource_id = settings.jarvis_asr_resource_id.strip()
        self._api_key = settings.jarvis_asr_api_key.strip()
        self._app_key = settings.jarvis_asr_app_key.strip()
        self._access_key = settings.jarvis_asr_access_key.strip()
        self._timeout = max(1.0, float(settings.jarvis_asr_timeout_seconds))
        self._user_uid = settings.jarvis_asr_user_uid.strip() or "jarvis"
        if not self._api_base_url:
            raise ProviderConfigError("Volcengine ASR is not configured: missing JARVIS_ASR_API_BASE_URL.")
        if not self._resource_id:
            raise ProviderConfigError("Volcengine ASR is not configured: missing JARVIS_ASR_RESOURCE_ID.")
        if not self._api_key and not (self._app_key and self._access_key):
            raise ProviderConfigError(
                "Volcengine ASR is not configured: provide JARVIS_ASR_API_KEY or both JARVIS_ASR_APP_KEY and JARVIS_ASR_ACCESS_KEY."
            )

    def transcribe(self, request: SpeechRecognitionRequest) -> TranscriptResult:
        audio_path = Path(request.path)
        if not audio_path.exists():
            raise ProviderRequestError(f"ASR input file is missing: {audio_path}")
        audio_format = _detect_audio_format(audio_path, request.mime_type)
        if not audio_format:
            raise ProviderRequestError(
                f"Volcengine ASR flash currently supports wav, mp3, and ogg_opus inputs. Unsupported file: {audio_path.name}"
            )

        audio_bytes = audio_path.read_bytes()
        body = {
            "user": {"uid": self._user_uid},
            "audio": {
                "data": base64.b64encode(audio_bytes).decode("ascii"),
                "format": audio_format,
            },
            "request": {
                "model_name": "bigmodel",
                "enable_punc": settings.jarvis_asr_enable_punc,
                "enable_itn": settings.jarvis_asr_enable_itn,
                "enable_ddc": settings.jarvis_asr_enable_ddc,
            },
        }
        request_id = str(uuid4())
        request_obj = urllib.request.Request(
            self._api_base_url,
            data=json.dumps(body).encode("utf-8"),
            headers=_request_headers(request_id, self._resource_id, self._api_key, self._app_key, self._access_key),
            method="POST",
        )
        try:
            with urllib.request.urlopen(
                request_obj,
                timeout=self._timeout,
                context=_build_ssl_context(),
            ) as response:
                payload = json.loads(response.read().decode("utf-8"))
                response_headers = dict(response.headers.items())
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")
            raise ProviderRequestError(f"Volcengine ASR failed: {exc.code} {detail}") from exc
        except urllib.error.URLError as exc:
            reason = exc.reason
            if isinstance(reason, ssl.SSLError):
                raise ProviderRequestError(
                    "Volcengine ASR TLS verification failed. Install `certifi` in the backend environment, "
                    "or configure JARVIS_ASR_CA_BUNDLE / SSL_CERT_FILE."
                ) from exc
            raise ProviderRequestError(f"Volcengine ASR failed: {reason}") from exc

        status_code = response_headers.get("X-Api-Status-Code") or response_headers.get("x-api-status-code") or ""
        status_message = response_headers.get("X-Api-Message") or response_headers.get("x-api-message") or ""
        if status_code and status_code != "20000000":
            raise ProviderRequestError(f"Volcengine ASR error {status_code}: {status_message or payload}")

        result = payload.get("result") if isinstance(payload, dict) else None
        audio_info = payload.get("audio_info") if isinstance(payload, dict) else None
        if not isinstance(result, dict):
            raise ProviderRequestError("Volcengine ASR returned an unexpected response payload.")

        utterances = result.get("utterances") if isinstance(result.get("utterances"), list) else []
        segments: list[TranscriptSegment] = []
        for utterance in utterances:
            if not isinstance(utterance, dict):
                continue
            segment_text = str(utterance.get("text") or "").strip()
            if not segment_text:
                continue
            segments.append(
                TranscriptSegment(
                    text=segment_text,
                    start_ms=_as_int(utterance.get("start_time")),
                    end_ms=_as_int(utterance.get("end_time")),
                )
            )

        duration_ms = _as_int(audio_info.get("duration")) if isinstance(audio_info, dict) else None
        return TranscriptResult(
            text=str(result.get("text") or "").strip(),
            segments=segments,
            provider_name=self.provider_name,
            metadata={
                "duration_ms": duration_ms,
                "resource_id": self._resource_id,
                "format": audio_format,
            },
        )


def _detect_audio_format(path: Path, mime_type: str) -> str | None:
    suffix = path.suffix.lower()
    if suffix in _SUPPORTED_FORMATS:
        return _SUPPORTED_FORMATS[suffix]
    lowered = (mime_type or "").lower()
    if lowered == "audio/wav" or lowered == "audio/x-wav":
        return "wav"
    if lowered == "audio/mpeg":
        return "mp3"
    if lowered in {"audio/ogg", "audio/opus"}:
        return "ogg_opus"
    return None


def _request_headers(
    request_id: str,
    resource_id: str,
    api_key: str,
    app_key: str,
    access_key: str,
) -> dict[str, str]:
    headers = {
        "Content-Type": "application/json",
        "X-Api-Resource-Id": resource_id,
        "X-Api-Request-Id": request_id,
    }
    if api_key:
        headers["X-Api-Key"] = api_key
    else:
        headers["X-Api-App-Key"] = app_key
        headers["X-Api-Access-Key"] = access_key
        headers["X-Api-Sequence"] = "-1"
    return headers


def _as_int(value: object) -> int | None:
    return int(value) if isinstance(value, (int, float)) else None


def _build_ssl_context() -> ssl.SSLContext:
    cafile = None
    try:
        import os

        cafile = os.getenv("JARVIS_ASR_CA_BUNDLE") or os.getenv("SSL_CERT_FILE") or os.getenv("OPENAI_CA_BUNDLE")
    except Exception:
        cafile = None
    if not cafile:
        try:
            import certifi

            cafile = certifi.where()
        except ImportError:
            cafile = None
    return ssl.create_default_context(cafile=cafile)
