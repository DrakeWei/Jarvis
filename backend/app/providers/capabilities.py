from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, AsyncIterator, Protocol


@dataclass(frozen=True)
class ImageGenerationRequest:
    prompt: str
    model: str
    size: str | None = None
    quality: str | None = None
    background: str | None = None
    asset_ids: list[str] = field(default_factory=list)
    mask_asset_id: str | None = None
    input_fidelity: str | None = None


@dataclass(frozen=True)
class ImageGenerationProviderResult:
    image_bytes: bytes
    mime_type: str
    revised_prompt: str | None = None
    provider_name: str = ""
    metadata: dict[str, object] = field(default_factory=dict)


@dataclass(frozen=True)
class SpeechSynthesisRequest:
    text: str
    voice: str | None = None
    audio_format: str = "mp3"
    speed: float = 1.0
    pitch: float = 1.0
    stream: bool = True


@dataclass(frozen=True)
class AudioChunkEvent:
    sequence: int
    audio_bytes: bytes
    audio_format: str
    is_final: bool = False
    subtitle: str | None = None
    metadata: dict[str, object] = field(default_factory=dict)


@dataclass(frozen=True)
class GeneratedSpeechResult:
    audio_bytes: bytes
    mime_type: str
    provider_name: str = ""
    metadata: dict[str, object] = field(default_factory=dict)


@dataclass(frozen=True)
class SpeechRecognitionRequest:
    asset_id: str
    mime_type: str
    path: str


@dataclass(frozen=True)
class TranscriptSegment:
    text: str
    start_ms: int | None = None
    end_ms: int | None = None
    speaker: str | None = None
    confidence: float | None = None


@dataclass(frozen=True)
class TranscriptResult:
    text: str
    segments: list[TranscriptSegment] = field(default_factory=list)
    language: str | None = None
    provider_name: str = ""
    metadata: dict[str, object] = field(default_factory=dict)


@dataclass(frozen=True)
class VideoSummaryResult:
    summary: str
    keyframes: list[dict[str, object]] = field(default_factory=list)
    provider_name: str = ""
    metadata: dict[str, object] = field(default_factory=dict)


@dataclass(frozen=True)
class VideoGenerationRequest:
    prompt: str
    duration_seconds: int | None = None
    aspect_ratio: str | None = None
    asset_ids: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class VideoGenerationJob:
    job_id: str
    status: str
    provider_name: str = ""
    metadata: dict[str, object] = field(default_factory=dict)


class ImageGenerationProvider(Protocol):
    def generate(self, request: ImageGenerationRequest) -> ImageGenerationProviderResult: ...

    def edit(self, request: ImageGenerationRequest) -> ImageGenerationProviderResult: ...


class SpeechSynthesisProvider(Protocol):
    def synthesize_stream(self, request: SpeechSynthesisRequest) -> AsyncIterator[AudioChunkEvent]: ...

    def synthesize_once(self, request: SpeechSynthesisRequest) -> GeneratedSpeechResult: ...


class SpeechRecognitionProvider(Protocol):
    def transcribe(self, request: SpeechRecognitionRequest) -> TranscriptResult: ...


class VideoUnderstandingProvider(Protocol):
    def summarize(self, asset_id: str, *, path: str, mime_type: str, metadata: dict[str, Any] | None = None) -> VideoSummaryResult: ...


class VideoGenerationProvider(Protocol):
    def generate(self, request: VideoGenerationRequest) -> VideoGenerationJob: ...

    def poll(self, job_id: str) -> VideoGenerationJob: ...
