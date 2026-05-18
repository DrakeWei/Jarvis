from __future__ import annotations

from app.providers import ProviderConfigError, ProviderRequestError, SpeechRecognitionRequest, TranscriptResult, create_speech_recognition_provider


class SpeechRecognitionError(RuntimeError):
    pass


def transcribe(request: SpeechRecognitionRequest) -> TranscriptResult:
    try:
        provider = create_speech_recognition_provider()
    except ProviderConfigError as exc:
        raise SpeechRecognitionError(str(exc)) from exc
    try:
        return provider.transcribe(request)
    except ProviderRequestError as exc:
        raise SpeechRecognitionError(str(exc)) from exc
