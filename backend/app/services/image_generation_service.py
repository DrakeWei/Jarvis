from __future__ import annotations

import base64
import hashlib
import io
import json
import ssl
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse

from PIL import Image

from app.core import session_assets as session_asset_utils
from app.core.config import settings
from app.schemas.assets import SessionAssetSummary
import app.services.asset_ingestion_service as asset_ingestion_service
import app.services.asset_service as asset_service


class ImageGenerationError(RuntimeError):
    pass


@dataclass(frozen=True)
class GeneratedImageResult:
    asset: SessionAssetSummary
    model: str
    prompt: str
    revised_prompt: str | None = None


def generate_image(
    session_id: str,
    prompt: str,
    *,
    asset_ids: list[str] | None = None,
    mask_asset_id: str | None = None,
    input_fidelity: str | None = None,
    size: str | None = None,
    background: str | None = None,
    quality: str | None = None,
) -> GeneratedImageResult:
    normalized_prompt = prompt.strip()
    if not normalized_prompt:
        raise ImageGenerationError("generate_image requires a non-empty prompt.")

    model = settings.jarvis_image_model.strip() or "gpt-image-2"
    resolved_size = _normalize_image_size(size)
    resolved_asset_ids = [str(asset_id).strip() for asset_id in (asset_ids or []) if str(asset_id).strip()]
    resolved_mask_asset_id = str(mask_asset_id or "").strip() or None
    resolved_input_fidelity = str(input_fidelity or "").strip().lower() or None
    if resolved_asset_ids:
        prepared_inputs = _prepare_edit_inputs(
            session_id,
            resolved_asset_ids,
            size=resolved_size,
            mask_asset_id=resolved_mask_asset_id,
        )
        request_body = {
            "model": model,
            "prompt": normalized_prompt,
            "size": resolved_size,
            "background": (background or settings.jarvis_image_default_background).strip() or "auto",
            "quality": (quality or settings.jarvis_image_default_quality).strip() or "auto",
            "output_format": "png",
            "n": 1,
            "images": prepared_inputs["images"],
        }
        if prepared_inputs.get("mask") is not None:
            request_body["mask"] = prepared_inputs["mask"]
        if resolved_input_fidelity:
            if resolved_input_fidelity not in {"high", "low"}:
                raise ImageGenerationError("input_fidelity must be either 'high' or 'low'.")
            request_body["input_fidelity"] = resolved_input_fidelity
        payload = _request_image_edit(request_body)
    else:
        if resolved_mask_asset_id:
            raise ImageGenerationError("mask_asset_id requires at least one source image asset.")
        if resolved_input_fidelity:
            raise ImageGenerationError("input_fidelity only applies when editing existing images.")
        request_body = {
            "model": model,
            "prompt": normalized_prompt,
            "size": resolved_size or _normalize_image_size(settings.jarvis_image_default_size) or "1024x1024",
            "background": (background or settings.jarvis_image_default_background).strip() or "auto",
            "quality": (quality or settings.jarvis_image_default_quality).strip() or "auto",
            "output_format": "png",
            "n": 1,
        }
        payload = _request_image_generation(request_body)
    data_items = payload.get("data")
    if not isinstance(data_items, list) or not data_items:
        raise ImageGenerationError("OpenAI image generation returned no image data.")

    item = data_items[0] if isinstance(data_items[0], dict) else {}
    encoded = str(item.get("b64_json") or "").strip()
    if not encoded:
        raise ImageGenerationError("OpenAI image generation returned an empty image payload.")

    try:
        image_bytes = base64.b64decode(encoded)
    except Exception as exc:
        raise ImageGenerationError("OpenAI image generation returned invalid base64 image data.") from exc

    sha256 = hashlib.sha256(image_bytes).hexdigest()
    filename = _generated_filename()
    asset = asset_service.create_asset_record(
        session_id,
        kind="image",
        origin="generated",
        source_asset_id=resolved_asset_ids[0] if resolved_asset_ids else None,
        metadata_json={
            "provider": "openai_compatible",
            "model": model,
            "prompt": normalized_prompt,
            "size": request_body.get("size"),
            "quality": request_body.get("quality"),
            "background": request_body.get("background"),
        },
        mime_type="image/png",
        filename=filename,
        size_bytes=len(image_bytes),
        sha256=sha256,
        status="uploaded",
    )
    session_asset_utils.ensure_asset_dirs(session_id, asset.id)
    original_path = Path(asset.storage_path)
    original_path.write_bytes(image_bytes)
    updated = asset_service.update_asset_record(
        asset.id,
        storage_path=original_path.as_posix(),
        sha256=sha256,
    ) or asset
    ingested = asset_ingestion_service.ingest_asset(updated.id)

    revised_prompt = str(item.get("revised_prompt") or "").strip() or None
    return GeneratedImageResult(
        asset=ingested,
        model=model,
        prompt=normalized_prompt,
        revised_prompt=revised_prompt,
    )


def _request_image_generation(request_body: dict[str, object]) -> dict[str, object]:
    endpoint = _endpoint_with_query("images/generations")
    return _request_openai_json(endpoint, request_body)


def _request_image_edit(request_body: dict[str, object]) -> dict[str, object]:
    endpoint = _endpoint_with_query("images/edits")
    return _request_openai_json(endpoint, request_body)


def _endpoint_with_query(path_suffix: str) -> str:
    endpoint = f"{_image_base_url().rstrip('/')}/{path_suffix.lstrip('/')}"
    query_params = _image_query_params()
    if query_params:
        endpoint = f"{endpoint}?{urllib.parse.urlencode(query_params)}"
    return endpoint


def _request_openai_json(endpoint: str, request_body: dict[str, object]) -> dict[str, object]:
    request = urllib.request.Request(
        endpoint,
        data=json.dumps(request_body).encode("utf-8"),
        headers=_openai_headers(),
        method="POST",
    )
    try:
        with urllib.request.urlopen(
            request,
            timeout=max(30, settings.jarvis_image_request_timeout_seconds),
            context=_build_ssl_context(),
        ) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise ImageGenerationError(f"OpenAI image generation failed: {exc.code} {detail}") from exc
    except urllib.error.URLError as exc:
        reason = exc.reason
        if isinstance(reason, ssl.SSLError):
            raise ImageGenerationError(
                "OpenAI image generation TLS verification failed. Install `certifi` in the backend environment, "
                "or configure OPENAI_CA_BUNDLE / SSL_CERT_FILE."
            ) from exc
        raise ImageGenerationError(f"OpenAI image generation failed: {reason}") from exc
    if not isinstance(payload, dict):
        raise ImageGenerationError("OpenAI image generation returned an unexpected response payload.")
    return payload


def _edit_images(session_id: str, asset_ids: list[str]) -> list[dict[str, str]]:
    images: list[dict[str, str]] = []
    for asset_id in asset_ids:
        asset = _load_session_image_asset(session_id, asset_id)
        images.append(_asset_image_url_part(asset))
    if not images:
        raise ImageGenerationError("Image edit requires at least one valid image asset.")
    return images


def _edit_mask(session_id: str, asset_id: str) -> dict[str, str]:
    asset = _load_session_image_asset(session_id, asset_id)
    if asset.mime_type != "image/png":
        raise ImageGenerationError("mask_asset_id must reference a PNG image asset.")
    return _asset_image_url_part(asset)


def _prepare_edit_inputs(
    session_id: str,
    asset_ids: list[str],
    *,
    size: str | None,
    mask_asset_id: str | None,
) -> dict[str, object]:
    if mask_asset_id:
        return {
            "images": _edit_images(session_id, asset_ids),
            "mask": _edit_mask(session_id, mask_asset_id),
        }
    if len(asset_ids) == 1 and size:
        auto = _build_outpaint_canvas_inputs(session_id, asset_ids[0], target_size=size)
        if auto is not None:
            return auto
    return {"images": _edit_images(session_id, asset_ids)}


def _load_session_image_asset(session_id: str, asset_id: str) -> SessionAssetSummary:
    asset = asset_service.get_asset(asset_id, session_id=session_id)
    if asset is None:
        raise ImageGenerationError(f"Unknown session image asset '{asset_id}'.")
    if asset.kind != "image":
        raise ImageGenerationError(f"Asset '{asset.filename}' is not an image and cannot be edited.")
    image_path = Path(asset.storage_path)
    if not image_path.exists():
        raise ImageGenerationError(f"Image asset '{asset.filename}' is missing from storage.")
    return asset


def _asset_image_url_part(asset: SessionAssetSummary) -> dict[str, str]:
    image_path = Path(asset.storage_path)
    encoded = base64.b64encode(image_path.read_bytes()).decode("ascii")
    return {"image_url": f"data:{asset.mime_type};base64,{encoded}"}


def _build_outpaint_canvas_inputs(session_id: str, asset_id: str, *, target_size: str) -> dict[str, object] | None:
    asset = _load_session_image_asset(session_id, asset_id)
    target = _parse_image_size(target_size)
    if target is None:
        return None
    target_width, target_height = target
    image_path = Path(asset.storage_path)
    with Image.open(image_path) as original:
        source = original.convert("RGBA")
        if source.width >= target_width or source.height >= target_height:
            return None
        canvas = Image.new("RGBA", (target_width, target_height), (0, 0, 0, 0))
        offset_x = (target_width - source.width) // 2
        offset_y = (target_height - source.height) // 2
        canvas.alpha_composite(source, (offset_x, offset_y))
        mask = Image.new("RGBA", (target_width, target_height), (0, 0, 0, 0))
        preserved = Image.new("RGBA", source.size, (255, 255, 255, 255))
        mask.alpha_composite(preserved, (offset_x, offset_y))
    return {
        "images": [{"image_url": _pil_image_data_url(canvas, "PNG")}],
        "mask": {"image_url": _pil_image_data_url(mask, "PNG")},
    }


def _pil_image_data_url(image: Image.Image, format_name: str) -> str:
    buffer = io.BytesIO()
    image.save(buffer, format=format_name)
    encoded = base64.b64encode(buffer.getvalue()).decode("ascii")
    mime_type = f"image/{format_name.lower()}"
    return f"data:{mime_type};base64,{encoded}"


def _openai_headers() -> dict[str, str]:
    headers = {
        "Content-Type": "application/json",
    }
    api_key = settings.jarvis_image_api_key or settings.openai_api_key
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    headers.update(_image_http_headers())
    return headers


def _image_base_url() -> str:
    explicit = settings.jarvis_image_base_url.strip()
    if explicit:
        return explicit
    if _should_reuse_openai_provider_for_images():
        return settings.openai_base_url
    return "https://api.openai.com/v1"


def _image_query_params() -> dict[str, str]:
    if settings.jarvis_image_query_params:
        return settings.jarvis_image_query_params
    if _should_reuse_openai_provider_for_images():
        return settings.openai_query_params
    return {}


def _image_http_headers() -> dict[str, str]:
    if settings.jarvis_image_http_headers:
        return settings.jarvis_image_http_headers
    if _should_reuse_openai_provider_for_images():
        return settings.openai_http_headers
    return {}


def _should_reuse_openai_provider_for_images() -> bool:
    parsed = urlparse(settings.openai_base_url)
    host = (parsed.netloc or "").lower()
    return host in {"api.openai.com", "platform.openai.com"}


def _normalize_image_size(size: str | None) -> str | None:
    raw = str(size or "").strip().lower()
    if not raw:
        return None
    if raw == "auto":
        return "auto"
    parsed = _parse_image_size(raw)
    if parsed is None:
        return None
    width, height = parsed
    if width == height:
        return "1024x1024"
    return "1024x1536" if height > width else "1536x1024"


def _parse_image_size(size: str) -> tuple[int, int] | None:
    try:
        left, right = size.lower().split("x", 1)
        width = int(left)
        height = int(right)
    except Exception:
        return None
    if width <= 0 or height <= 0:
        return None
    return width, height


def _build_ssl_context() -> ssl.SSLContext:
    cafile = None
    try:
        import os

        cafile = os.getenv("OPENAI_CA_BUNDLE") or os.getenv("SSL_CERT_FILE")
    except Exception:
        cafile = None
    if not cafile:
        try:
            import certifi

            cafile = certifi.where()
        except ImportError:
            cafile = None
    return ssl.create_default_context(cafile=cafile)


def _generated_filename() -> str:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S")
    return f"generated-{stamp}.png"
