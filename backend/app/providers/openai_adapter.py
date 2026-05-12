from __future__ import annotations

import json
import ssl
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from typing import Any

from app.core.config import settings
from app.providers.base import BaseAdapter, LLMResponse, ProviderConfigError, ProviderRequestError, TextBlock, ToolUseBlock


@dataclass
class OpenAISettings:
    base_url: str
    wire_api: str
    query_params: dict[str, str]
    http_headers: dict[str, str]


class OpenAIAdapter(BaseAdapter):
    def __init__(self) -> None:
        super().__init__()
        self._api_key = settings.openai_api_key
        self._settings = OpenAISettings(
            base_url=settings.openai_base_url or "https://api.openai.com/v1",
            wire_api=settings.openai_wire_api or "chat_completions",
            query_params=settings.openai_query_params,
            http_headers=settings.openai_http_headers,
        )
        self._endpoint = _openai_endpoint(self._settings)
        self._ssl_context = _build_ssl_context()

    def create(
        self,
        *,
        model: str,
        messages: list[dict[str, Any]],
        system: str | None = None,
        tools: list[dict[str, Any]] | None = None,
        max_tokens: int = 8000,
    ) -> LLMResponse:
        if not model:
            raise ProviderConfigError("LLM provider is not configured: missing MODEL_ID.")

        body = _openai_request_body(
            wire_api=self._settings.wire_api,
            model=model,
            messages=messages,
            system=system,
            tools=tools,
            max_tokens=max_tokens,
        )
        request = urllib.request.Request(
            self._endpoint,
            data=json.dumps(body).encode("utf-8"),
            headers=_openai_headers(self._api_key, self._settings.http_headers),
            method="POST",
        )

        try:
            with urllib.request.urlopen(request, timeout=120, context=self._ssl_context) as response:
                payload = json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")
            raise ProviderRequestError(f"OpenAI-compatible request failed: {exc.code} {detail}") from exc
        except urllib.error.URLError as exc:
            reason = exc.reason
            if isinstance(reason, ssl.SSLError):
                raise ProviderRequestError(
                    "OpenAI-compatible TLS verification failed. Install `certifi` in the backend environment, "
                    "or configure OPENAI_CA_BUNDLE / SSL_CERT_FILE."
                ) from exc
            raise ProviderRequestError(f"OpenAI-compatible request failed: {reason}") from exc

        return _parse_openai_response(payload, wire_api=self._settings.wire_api)

    def stream_text(
        self,
        *,
        model: str,
        messages: list[dict[str, Any]],
        system: str | None = None,
        max_tokens: int = 8000,
    ):
        if not model:
            raise ProviderConfigError("LLM provider is not configured: missing MODEL_ID.")

        body = _openai_request_body(
            wire_api=self._settings.wire_api,
            model=model,
            messages=messages,
            system=system,
            tools=None,
            max_tokens=max_tokens,
        )
        body["stream"] = True
        request = urllib.request.Request(
            self._endpoint,
            data=json.dumps(body).encode("utf-8"),
            headers=_openai_headers(self._api_key, self._settings.http_headers),
            method="POST",
        )

        try:
            with urllib.request.urlopen(request, timeout=120, context=self._ssl_context) as response:
                if self._settings.wire_api == "responses":
                    yield from _stream_responses_text(response)
                else:
                    yield from _stream_chat_completions_text(response)
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")
            raise ProviderRequestError(f"OpenAI-compatible request failed: {exc.code} {detail}") from exc
        except urllib.error.URLError as exc:
            reason = exc.reason
            if isinstance(reason, ssl.SSLError):
                raise ProviderRequestError(
                    "OpenAI-compatible TLS verification failed. Install `certifi` in the backend environment, "
                    "or configure OPENAI_CA_BUNDLE / SSL_CERT_FILE."
                ) from exc
            raise ProviderRequestError(f"OpenAI-compatible request failed: {reason}") from exc


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


def _openai_endpoint(settings_value: OpenAISettings) -> str:
    wire_api = settings_value.wire_api.strip().lower()
    if wire_api == "responses":
        suffix = "responses"
    elif wire_api == "chat_completions":
        suffix = "chat/completions"
    else:
        raise ProviderConfigError(f"Unsupported OPENAI_WIRE_API: {settings_value.wire_api}")
    endpoint = f"{settings_value.base_url.rstrip('/')}/{suffix}"
    if settings_value.query_params:
        endpoint = f"{endpoint}?{urllib.parse.urlencode(settings_value.query_params)}"
    return endpoint


def _openai_headers(api_key: str, extra_headers: dict[str, str]) -> dict[str, str]:
    headers = {
        "Content-Type": "application/json",
    }
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    headers.update(extra_headers)
    return headers


def _openai_request_body(
    *,
    wire_api: str,
    model: str,
    messages: list[dict[str, Any]],
    system: str | None,
    tools: list[dict[str, Any]] | None,
    max_tokens: int,
) -> dict[str, Any]:
    if wire_api == "responses":
        body: dict[str, Any] = {
            "model": model,
            "input": _responses_input(messages, system=system),
            "max_output_tokens": max_tokens,
        }
        if tools:
            body["tools"] = [_responses_tool(tool) for tool in tools]
        return body

    body = {
        "model": model,
        "messages": _openai_messages(messages, system=system),
        "max_tokens": max_tokens,
    }
    if tools:
        body["tools"] = [_openai_tool(tool) for tool in tools]
        body["tool_choice"] = "auto"
    return body


def _parse_openai_response(payload: dict[str, Any], *, wire_api: str) -> LLMResponse:
    if wire_api == "responses":
        return _parse_responses_payload(payload)
    return _parse_chat_completions_payload(payload)


def _openai_messages(messages: list[dict[str, Any]], *, system: str | None = None) -> list[dict[str, Any]]:
    converted: list[dict[str, Any]] = []
    if system:
        converted.append({"role": "system", "content": system})

    for message in messages:
        role = message["role"]
        content = message.get("content", "")
        if isinstance(content, str):
            converted.append({"role": role, "content": content})
            continue
        if not isinstance(content, list):
            converted.append({"role": role, "content": str(content)})
            continue

        if role == "assistant":
            converted.append(_openai_assistant_message(content))
            continue

        if role == "user":
            pending_text: list[str] = []
            for part in content:
                if isinstance(part, dict) and part.get("type") == "tool_result":
                    if pending_text:
                        converted.append({"role": "user", "content": "\n".join(pending_text)})
                        pending_text = []
                    converted.append(
                        {
                            "role": "tool",
                            "tool_call_id": str(part["tool_use_id"]),
                            "content": str(part.get("content", "")),
                        }
                    )
                    continue
                text = _part_text(part)
                if text:
                    pending_text.append(text)
            if pending_text:
                converted.append({"role": "user", "content": "\n".join(pending_text)})
            continue

        converted.append({"role": role, "content": "\n".join(_part_text(part) for part in content)})

    return converted


def _responses_input(messages: list[dict[str, Any]], *, system: str | None = None) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    if system:
        items.append(_responses_message("system", [system]))
    for message in messages:
        role = message["role"]
        content = message.get("content", "")

        if isinstance(content, str):
            items.append(_responses_message(role, [content]))
            continue
        if not isinstance(content, list):
            items.append(_responses_message(role, [str(content)]))
            continue

        if role == "assistant":
            text_parts: list[str] = []
            for part in content:
                if isinstance(part, ToolUseBlock):
                    items.append(
                        {
                            "type": "function_call",
                            "call_id": part.id,
                            "name": part.name,
                            "arguments": json.dumps(part.input or {}),
                        }
                    )
                    continue
                if isinstance(part, dict) and part.get("type") == "tool_use":
                    items.append(
                        {
                            "type": "function_call",
                            "call_id": str(part["id"]),
                            "name": str(part["name"]),
                            "arguments": json.dumps(dict(part.get("input", {}) or {})),
                        }
                    )
                    continue
                text = _part_text(part)
                if text:
                    text_parts.append(text)
            if text_parts:
                items.append(_responses_message("assistant", text_parts))
            continue

        if role == "user":
            text_parts: list[str] = []
            for part in content:
                if isinstance(part, dict) and part.get("type") == "tool_result":
                    if text_parts:
                        items.append(_responses_message("user", text_parts))
                        text_parts = []
                    items.append(
                        {
                            "type": "function_call_output",
                            "call_id": str(part["tool_use_id"]),
                            "output": str(part.get("content", "")),
                        }
                    )
                    continue
                text = _part_text(part)
                if text:
                    text_parts.append(text)
            if text_parts:
                items.append(_responses_message("user", text_parts))
            continue

        items.append(_responses_message(role, [_part_text(part) for part in content if _part_text(part)]))
    return items


def _responses_message(role: str, texts: list[str]) -> dict[str, Any]:
    content_type = "output_text" if role == "assistant" else "input_text"
    return {
        "role": role,
        "content": [{"type": content_type, "text": text} for text in texts if text],
    }


def _responses_tool(tool: dict[str, Any]) -> dict[str, Any]:
    return {
        "type": "function",
        "name": tool["name"],
        "description": tool.get("description", ""),
        "parameters": tool.get("input_schema", {"type": "object", "properties": {}}),
    }


def _openai_tool(tool: dict[str, Any]) -> dict[str, Any]:
    return {
        "type": "function",
        "function": {
            "name": tool["name"],
            "description": tool.get("description", ""),
            "parameters": tool.get("input_schema", {"type": "object", "properties": {}}),
        },
    }


def _openai_assistant_message(content: list[Any]) -> dict[str, Any]:
    text_parts: list[str] = []
    tool_calls: list[dict[str, Any]] = []
    for part in content:
        if isinstance(part, ToolUseBlock):
            tool_calls.append(
                {
                    "id": part.id,
                    "type": "function",
                    "function": {
                        "name": part.name,
                        "arguments": json.dumps(part.input or {}),
                    },
                }
            )
            continue
        if isinstance(part, dict) and part.get("type") == "tool_use":
            tool_calls.append(
                {
                    "id": str(part["id"]),
                    "type": "function",
                    "function": {
                        "name": str(part["name"]),
                        "arguments": json.dumps(dict(part.get("input", {}) or {})),
                    },
                }
            )
            continue
        text = _part_text(part)
        if text:
            text_parts.append(text)

    message: dict[str, Any] = {
        "role": "assistant",
        "content": "\n".join(text_parts) if text_parts else None,
    }
    if tool_calls:
        message["tool_calls"] = tool_calls
    return message


def _parse_chat_completions_payload(payload: dict[str, Any]) -> LLMResponse:
    choice = payload["choices"][0]
    message = choice["message"]
    blocks: list[TextBlock | ToolUseBlock] = []

    content = message.get("content")
    for text in _openai_text_segments(content):
        blocks.append(TextBlock(text=text))

    for tool_call in message.get("tool_calls", []):
        raw_args = tool_call.get("function", {}).get("arguments") or "{}"
        try:
            parsed_args = json.loads(raw_args)
        except json.JSONDecodeError:
            parsed_args = {"_raw": raw_args}
        blocks.append(
            ToolUseBlock(
                id=str(tool_call["id"]),
                name=str(tool_call["function"]["name"]),
                input=parsed_args if isinstance(parsed_args, dict) else {"value": parsed_args},
            )
        )

    stop_reason = "tool_use" if any(block.type == "tool_use" for block in blocks) else str(
        choice.get("finish_reason") or "stop"
    )
    return LLMResponse(content=blocks, stop_reason=stop_reason)


def _parse_responses_payload(payload: dict[str, Any]) -> LLMResponse:
    blocks: list[TextBlock | ToolUseBlock] = []
    for item in payload.get("output", []):
        item_type = item.get("type")
        if item_type == "message":
            for part in item.get("content", []):
                text = None
                if isinstance(part, dict):
                    if part.get("type") == "output_text":
                        text = part.get("text")
                    elif part.get("type") == "text":
                        text = part.get("text")
                if text:
                    blocks.append(TextBlock(text=str(text)))
            continue
        if item_type == "function_call":
            raw_args = item.get("arguments") or "{}"
            try:
                parsed_args = json.loads(raw_args)
            except json.JSONDecodeError:
                parsed_args = {"_raw": raw_args}
            blocks.append(
                ToolUseBlock(
                    id=str(item.get("call_id") or item.get("id") or ""),
                    name=str(item.get("name") or ""),
                    input=parsed_args if isinstance(parsed_args, dict) else {"value": parsed_args},
                )
            )

    stop_reason = "tool_use" if any(block.type == "tool_use" for block in blocks) else str(
        payload.get("status") or "stop"
    )
    return LLMResponse(content=blocks, stop_reason=stop_reason)


def _openai_text_segments(content: Any) -> list[str]:
    if content is None:
        return []
    if isinstance(content, str):
        return [content] if content else []
    if isinstance(content, list):
        texts = []
        for item in content:
            if isinstance(item, dict):
                if item.get("type") == "text" and item.get("text"):
                    texts.append(str(item["text"]))
                elif item.get("type") == "output_text" and item.get("text"):
                    texts.append(str(item["text"]))
        return texts
    return [str(content)]


def _part_text(part: Any) -> str:
    if isinstance(part, TextBlock):
        return part.text
    if isinstance(part, dict) and part.get("type") == "text":
        return str(part.get("text", ""))
    if isinstance(part, ToolUseBlock):
        return ""
    return str(part)


def _stream_responses_text(response) -> Any:
    for raw in response:
        line = raw.decode("utf-8", errors="replace").strip()
        if not line or not line.startswith("data: "):
            continue
        payload_text = line[6:]
        if payload_text == "[DONE]":
            break
        payload = json.loads(payload_text)
        if payload.get("type") == "response.output_text.delta":
            delta = payload.get("delta")
            if delta:
                yield str(delta)


def _stream_chat_completions_text(response) -> Any:
    for raw in response:
        line = raw.decode("utf-8", errors="replace").strip()
        if not line or not line.startswith("data: "):
            continue
        payload_text = line[6:]
        if payload_text == "[DONE]":
            break
        payload = json.loads(payload_text)
        choices = payload.get("choices") or []
        if not choices:
            continue
        delta = choices[0].get("delta", {}).get("content")
        if delta:
            yield str(delta)
