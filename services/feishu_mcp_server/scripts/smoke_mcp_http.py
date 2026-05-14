#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
import urllib.error
import urllib.request
from typing import Any


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Smoke-test the local Feishu MCP HTTP server.")
    parser.add_argument("--base-url", default="http://127.0.0.1:8765/mcp")
    parser.add_argument("--health-url", default="http://127.0.0.1:8765/health")
    parser.add_argument("--token", default="")
    subparsers = parser.add_subparsers(dest="command", required=True)

    subparsers.add_parser("health")
    subparsers.add_parser("tools")

    call_parser = subparsers.add_parser("call")
    call_parser.add_argument("--tool", required=True)
    call_parser.add_argument("--args", default="{}")
    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()

    if args.command == "health":
        payload = http_get(args.health_url, token=args.token)
        print_json(payload)
        return 0

    client = MCPHTTPClient(base_url=args.base_url, token=args.token)
    initialize = client.initialize()
    print_json({"initialize": initialize})

    tools = client.list_tools()
    if args.command == "tools":
        print_json({"tools": tools})
        return 0

    try:
        tool_args = json.loads(args.args)
    except json.JSONDecodeError as exc:
        print(f"Invalid --args JSON: {exc}", file=sys.stderr)
        return 2
    if not isinstance(tool_args, dict):
        print("--args must decode to a JSON object.", file=sys.stderr)
        return 2
    result = client.call_tool(args.tool, tool_args)
    print_json({"result": result})
    return 0


class MCPHTTPClient:
    def __init__(self, *, base_url: str, token: str = "") -> None:
        self.base_url = base_url
        self.token = token
        self._request_id = 1

    def initialize(self) -> dict[str, Any]:
        response = self._post(
            {
                "jsonrpc": "2.0",
                "id": self._next_id(),
                "method": "initialize",
                "params": {
                    "protocolVersion": "2025-03-26",
                    "capabilities": {},
                    "clientInfo": {
                        "name": "feishu-mcp-smoke",
                        "version": "0.1.0",
                    },
                },
            }
        )
        self._post(
            {
                "jsonrpc": "2.0",
                "method": "notifications/initialized",
            }
        )
        return response

    def list_tools(self) -> dict[str, Any]:
        return self._post(
            {
                "jsonrpc": "2.0",
                "id": self._next_id(),
                "method": "tools/list",
                "params": {},
            }
        )

    def call_tool(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        return self._post(
            {
                "jsonrpc": "2.0",
                "id": self._next_id(),
                "method": "tools/call",
                "params": {
                    "name": name,
                    "arguments": arguments,
                },
            }
        )

    def _post(self, body: dict[str, Any]) -> dict[str, Any]:
        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
        }
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        request = urllib.request.Request(
            self.base_url,
            data=json.dumps(body).encode("utf-8"),
            headers=headers,
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=20) as response:
                raw = response.read().decode("utf-8")
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")
            raise SystemExit(f"HTTP {exc.code}: {detail}") from exc
        except urllib.error.URLError as exc:
            raise SystemExit(f"Request failed: {exc.reason}") from exc
        payload = json.loads(raw) if raw.strip() else {}
        if not isinstance(payload, dict):
            raise SystemExit("Server returned non-object JSON.")
        return payload

    def _next_id(self) -> int:
        request_id = self._request_id
        self._request_id += 1
        return request_id


def http_get(url: str, *, token: str = "") -> dict[str, Any]:
    headers: dict[str, str] = {}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    request = urllib.request.Request(url, headers=headers, method="GET")
    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            raw = response.read().decode("utf-8")
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise SystemExit(f"HTTP {exc.code}: {detail}") from exc
    except urllib.error.URLError as exc:
        raise SystemExit(f"Request failed: {exc.reason}") from exc
    payload = json.loads(raw) if raw.strip() else {}
    if not isinstance(payload, dict):
        raise SystemExit("Server returned non-object JSON.")
    return payload


def print_json(payload: dict[str, Any]) -> None:
    print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))


if __name__ == "__main__":
    raise SystemExit(main())
