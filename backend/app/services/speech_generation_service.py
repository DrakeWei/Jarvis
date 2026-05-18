from __future__ import annotations

import hashlib
import mimetypes
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from app.core import session_assets as session_asset_utils
from app.providers import GeneratedSpeechResult, ProviderConfigError, SpeechSynthesisRequest, create_speech_synthesis_provider
from app.schemas.assets import SessionAssetSummary
import app.services.asset_service as asset_service


class SpeechGenerationError(RuntimeError):
    pass


@dataclass(frozen=True)
class GeneratedSpeechAssetResult:
    asset: SessionAssetSummary
    provider_name: str
    request: SpeechSynthesisRequest


def generate_speech(session_id: str, request: SpeechSynthesisRequest) -> GeneratedSpeechAssetResult:
    normalized_text = request.text.strip()
    if not normalized_text:
        raise SpeechGenerationError("generate_speech requires a non-empty text value.")
    try:
        provider = create_speech_synthesis_provider()
    except ProviderConfigError as exc:
        raise SpeechGenerationError(str(exc)) from exc
    try:
        generated = provider.synthesize_once(request)
    except Exception as exc:
        raise SpeechGenerationError(str(exc)) from exc
    if not generated.audio_bytes:
        raise SpeechGenerationError("Speech provider returned an empty audio payload.")

    mime_type = _normalize_audio_mime_type(generated, request.audio_format)
    filename = _generated_audio_filename(request.audio_format, mime_type)
    sha256 = hashlib.sha256(generated.audio_bytes).hexdigest()
    metadata_json = {
        "provider": generated.provider_name,
        "voice": request.voice,
        "format": request.audio_format,
        "speed": request.speed,
        "pitch": request.pitch,
        **(generated.metadata or {}),
    }
    asset = asset_service.create_asset_record(
        session_id,
        kind="generated_audio",
        origin="generated",
        mime_type=mime_type,
        filename=filename,
        size_bytes=len(generated.audio_bytes),
        sha256=sha256,
        status="ready",
        metadata_json=metadata_json,
    )
    session_asset_utils.ensure_asset_dirs(session_id, asset.id)
    original_path = Path(asset.storage_path)
    original_path.write_bytes(generated.audio_bytes)
    persisted = asset_service.update_asset_record(
        asset.id,
        storage_path=original_path.as_posix(),
        sha256=sha256,
        metadata_json=metadata_json,
    ) or asset
    return GeneratedSpeechAssetResult(
        asset=persisted,
        provider_name=generated.provider_name,
        request=request,
    )


def _normalize_audio_mime_type(generated: GeneratedSpeechResult, requested_format: str) -> str:
    mime_type = str(generated.mime_type or "").strip().lower()
    if mime_type:
        return mime_type
    normalized_format = str(requested_format or "").strip().lower()
    guessed, _ = mimetypes.guess_type(f"audio.{normalized_format}") if normalized_format else (None, None)
    return (guessed or "audio/mpeg").lower()


def _generated_audio_filename(audio_format: str, mime_type: str) -> str:
    normalized_format = str(audio_format or "").strip().lower()
    if not normalized_format:
        extension = mimetypes.guess_extension(mime_type) or ".mp3"
    else:
        extension = normalized_format if normalized_format.startswith(".") else f".{normalized_format}"
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    return f"generated-speech-{timestamp}{extension}"
