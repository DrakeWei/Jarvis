from __future__ import annotations

from app.providers import ProviderConfigError, VideoSummaryResult, create_video_understanding_provider


class VideoUnderstandingError(RuntimeError):
    pass


def summarize_video(asset_id: str, *, path: str, mime_type: str, metadata: dict[str, object] | None = None) -> VideoSummaryResult:
    try:
        provider = create_video_understanding_provider()
    except ProviderConfigError as exc:
        raise VideoUnderstandingError(str(exc)) from exc
    return provider.summarize(asset_id, path=path, mime_type=mime_type, metadata=metadata)
