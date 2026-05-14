from __future__ import annotations

import itertools
from dataclasses import dataclass
from typing import Any

from app.mcp.transport_http import MCPTransportError, HTTPJSONRPCTransport

PROTOCOL_VERSION = "2025-03-26"


class MCPClientError(RuntimeError):
    pass


@dataclass(frozen=True)
class RemoteTool:
    name: str
    description: str
    input_schema: dict[str, Any]
    title: str | None = None


@dataclass(frozen=True)
class RemoteToolCallResult:
    is_error: bool
    text: str
    raw_result: dict[str, Any]
    request_id: str


class MCPHTTPClient:
    def __init__(
        self,
        *,
        base_url: str,
        bearer_token: str = "",
        timeout_ms: int = 10000,
        client_name: str = "jarvis",
        client_version: str = "0.1.0",
    ) -> None:
        self._transport = HTTPJSONRPCTransport(
            base_url=base_url,
            bearer_token=bearer_token,
            timeout_ms=timeout_ms,
        )
        self._client_name = client_name
        self._client_version = client_version
        self._request_ids = itertools.count(1)

    def list_tools(self) -> list[RemoteTool]:
        self._initialize()
        cursor: str | None = None
        tools: list[RemoteTool] = []
        while True:
            params: dict[str, Any] = {}
            if cursor:
                params["cursor"] = cursor
            result, _request_id = self._request("tools/list", params)
            tool_items = result.get("tools", [])
            if not isinstance(tool_items, list):
                raise MCPClientError("MCP server returned an invalid tools/list response.")
            for item in tool_items:
                if not isinstance(item, dict):
                    continue
                name = str(item.get("name") or "").strip()
                if not name:
                    continue
                schema = item.get("inputSchema", {"type": "object", "properties": {}})
                if not isinstance(schema, dict):
                    schema = {"type": "object", "properties": {}}
                tools.append(
                    RemoteTool(
                        name=name,
                        title=str(item.get("title") or "").strip() or None,
                        description=str(item.get("description") or "").strip(),
                        input_schema=schema,
                    )
                )
            next_cursor = result.get("nextCursor")
            if not isinstance(next_cursor, str) or not next_cursor.strip():
                break
            cursor = next_cursor
        return tools

    def call_tool(self, remote_name: str, arguments: dict[str, Any]) -> RemoteToolCallResult:
        self._initialize()
        result, request_id = self._request(
            "tools/call",
            {
                "name": remote_name,
                "arguments": arguments,
            },
        )
        content = result.get("content", [])
        text_parts: list[str] = []
        if isinstance(content, list):
            for item in content:
                if not isinstance(item, dict):
                    continue
                if str(item.get("type") or "") != "text":
                    continue
                text = str(item.get("text") or "").strip()
                if text:
                    text_parts.append(text)
        if not text_parts and result.get("structuredContent") is not None:
            text_parts.append(str(result["structuredContent"]))
        text = "\n\n".join(text_parts).strip() or "MCP tool returned no text content."
        return RemoteToolCallResult(
            is_error=bool(result.get("isError", False)),
            text=text,
            raw_result=result,
            request_id=str(request_id),
        )

    def _initialize(self) -> None:
        self._request(
            "initialize",
            {
                "protocolVersion": PROTOCOL_VERSION,
                "capabilities": {},
                "clientInfo": {
                    "name": self._client_name,
                    "version": self._client_version,
                },
            },
        )
        self._notify("notifications/initialized")

    def _notify(self, method: str, params: dict[str, Any] | None = None) -> None:
        body: dict[str, Any] = {
            "jsonrpc": "2.0",
            "method": method,
        }
        if params:
            body["params"] = params
        try:
            self._transport.post(body)
        except MCPTransportError:
            # Notifications are best-effort in this minimal client.
            return

    def _request(self, method: str, params: dict[str, Any]) -> tuple[dict[str, Any], int]:
        request_id = next(self._request_ids)
        body = {
            "jsonrpc": "2.0",
            "id": request_id,
            "method": method,
            "params": params,
        }
        try:
            response = self._transport.post(body)
        except MCPTransportError as exc:
            raise MCPClientError(str(exc)) from exc
        if response.payload is None:
            raise MCPClientError("MCP server returned an empty response body.")
        if "error" in response.payload:
            error = response.payload.get("error") or {}
            if isinstance(error, dict):
                code = error.get("code", "unknown")
                message = error.get("message", "Unknown MCP error")
                raise MCPClientError(f"MCP request failed: {code} {message}")
            raise MCPClientError("MCP request failed with an unknown error response.")
        result = response.payload.get("result")
        if not isinstance(result, dict):
            raise MCPClientError("MCP server returned a malformed JSON-RPC result.")
        return result, request_id
