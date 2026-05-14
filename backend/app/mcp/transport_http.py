from __future__ import annotations

import json
import ssl
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Any


class MCPTransportError(RuntimeError):
    pass


@dataclass(frozen=True)
class HTTPResponse:
    status_code: int
    payload: dict[str, Any] | None


class HTTPJSONRPCTransport:
    def __init__(self, *, base_url: str, bearer_token: str = "", timeout_ms: int = 10000) -> None:
        self.base_url = base_url.rstrip("/")
        self.bearer_token = bearer_token.strip()
        self.timeout_seconds = max(timeout_ms, 1000) / 1000.0
        self._ssl_context = ssl.create_default_context()

    def post(self, body: dict[str, Any]) -> HTTPResponse:
        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
        }
        if self.bearer_token:
            headers["Authorization"] = f"Bearer {self.bearer_token}"

        request = urllib.request.Request(
            self.base_url,
            data=json.dumps(body).encode("utf-8"),
            headers=headers,
            method="POST",
        )

        try:
            with urllib.request.urlopen(request, timeout=self.timeout_seconds, context=self._ssl_context) as response:
                raw = response.read().decode("utf-8").strip()
                payload = json.loads(raw) if raw else None
                if payload is not None and not isinstance(payload, dict):
                    raise MCPTransportError("MCP server returned a non-object JSON response.")
                return HTTPResponse(status_code=response.getcode(), payload=payload)
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")
            raise MCPTransportError(f"MCP HTTP request failed: {exc.code} {detail}") from exc
        except urllib.error.URLError as exc:
            raise MCPTransportError(f"MCP HTTP request failed: {exc.reason}") from exc
        except json.JSONDecodeError as exc:
            raise MCPTransportError("MCP server returned invalid JSON.") from exc
