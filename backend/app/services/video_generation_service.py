from __future__ import annotations

from app.providers import ProviderConfigError, VideoGenerationJob, VideoGenerationRequest, create_video_generation_provider


class VideoGenerationError(RuntimeError):
    pass


def submit_video_generation(request: VideoGenerationRequest) -> VideoGenerationJob:
    try:
        provider = create_video_generation_provider()
    except ProviderConfigError as exc:
        raise VideoGenerationError(str(exc)) from exc
    return provider.generate(request)


def poll_video_generation(job_id: str) -> VideoGenerationJob:
    try:
        provider = create_video_generation_provider()
    except ProviderConfigError as exc:
        raise VideoGenerationError(str(exc)) from exc
    return provider.poll(job_id)
