from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class TextBlock:
    text: str
    type: str = field(default="text", init=False)


@dataclass
class ToolUseBlock:
    id: str
    name: str
    input: dict[str, Any]
    type: str = field(default="tool_use", init=False)


@dataclass
class LLMResponse:
    content: list[TextBlock | ToolUseBlock]
    stop_reason: str


class ProviderConfigError(RuntimeError):
    pass


class ProviderRequestError(RuntimeError):
    pass


class _MessagesAPI:
    def __init__(self, adapter: "BaseAdapter"):
        self._adapter = adapter

    def create(self, **kwargs) -> LLMResponse:
        return self._adapter.create(**kwargs)


class BaseAdapter:
    def __init__(self) -> None:
        self.messages = _MessagesAPI(self)

    def create(
        self,
        *,
        model: str,
        messages: list[dict[str, Any]],
        system: str | None = None,
        tools: list[dict[str, Any]] | None = None,
        max_tokens: int = 8000,
    ) -> LLMResponse:
        raise NotImplementedError

    def stream_text(
        self,
        *,
        model: str,
        messages: list[dict[str, Any]],
        system: str | None = None,
        max_tokens: int = 8000,
    ):
        raise NotImplementedError

    def stream_response(
        self,
        *,
        model: str,
        messages: list[dict[str, Any]],
        system: str | None = None,
        tools: list[dict[str, Any]] | None = None,
        max_tokens: int = 8000,
    ):
        raise NotImplementedError
