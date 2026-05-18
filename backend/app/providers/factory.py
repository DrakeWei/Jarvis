from app.core.config import settings
from app.providers.base import BaseAdapter, ProviderConfigError
from app.providers.capabilities import (
    ImageGenerationProvider,
    SpeechRecognitionProvider,
    SpeechSynthesisProvider,
    VideoGenerationProvider,
    VideoUnderstandingProvider,
)
from app.providers.openai_adapter import OpenAIAdapter
from app.providers.volcengine_asr_provider import VolcengineASRProvider
from app.providers.volcengine_tts_provider import VolcengineTTSProvider


def create_llm_adapter() -> BaseAdapter:
    return OpenAIAdapter()


def create_client() -> BaseAdapter:
    return create_llm_adapter()


def create_image_generation_provider() -> ImageGenerationProvider:
    provider_name = settings.jarvis_image_provider
    raise ProviderConfigError(f"Image generation provider '{provider_name or 'unset'}' is not wired yet.")


def create_speech_synthesis_provider() -> SpeechSynthesisProvider:
    provider_name = settings.jarvis_speech_synth_provider
    if provider_name == "volcengine":
        return VolcengineTTSProvider()
    raise ProviderConfigError(f"Speech synthesis provider '{provider_name or 'unset'}' is not wired yet.")


def create_speech_recognition_provider() -> SpeechRecognitionProvider:
    provider_name = settings.jarvis_speech_recognition_provider
    if provider_name == "volcengine":
        return VolcengineASRProvider()
    raise ProviderConfigError(f"Speech recognition provider '{provider_name or 'unset'}' is not wired yet.")


def create_video_understanding_provider() -> VideoUnderstandingProvider:
    provider_name = settings.jarvis_video_understanding_provider
    raise ProviderConfigError(f"Video understanding provider '{provider_name or 'unset'}' is not wired yet.")


def create_video_generation_provider() -> VideoGenerationProvider:
    provider_name = settings.jarvis_video_generation_provider
    raise ProviderConfigError(f"Video generation provider '{provider_name or 'unset'}' is not wired yet.")
