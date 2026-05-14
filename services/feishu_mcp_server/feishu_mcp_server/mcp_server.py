from __future__ import annotations

from typing import Any

from fastapi import FastAPI, Header, HTTPException, Response

from feishu_mcp_server.config import settings
from feishu_mcp_server.doc_service import FeishuDocServiceError, create_doc, get_doc, not_implemented, read_doc
from feishu_mcp_server.feishu_client import feishu_client

app = FastAPI(title=settings.app_name)

TOOL_DEFINITIONS: list[dict[str, Any]] = []

TOOL_DEFINITIONS.extend(
    [
        {
            "name": "feishu_doc_create",
            "title": "Create Feishu Doc",
            "description": "Create a Feishu upgraded doc and optionally seed it with initial blocks.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "title": {"type": "string"},
                    "folder_token": {"type": "string"},
                    "initial_blocks": {"type": "array"},
                },
                "required": ["title"],
            },
        },
        {
            "name": "feishu_doc_get",
            "title": "Get Feishu Doc Metadata",
            "description": "Resolve a Feishu document by document_id or document_url and return metadata.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "document_id": {"type": "string"},
                    "document_url": {"type": "string"},
                },
            },
        },
        {
            "name": "feishu_doc_read",
            "title": "Read Feishu Doc",
            "description": "Read a Feishu upgraded doc and return a linearized text-friendly representation.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "document_id": {"type": "string"},
                    "document_url": {"type": "string"},
                    "max_blocks": {"type": "integer"},
                },
            },
        },
    ]
)


@app.get("/health")
def health() -> dict[str, object]:
    auth_status = feishu_client.auth_status()
    return {
        "status": "ok",
        "app": settings.app_name,
        "version": settings.app_version,
        "protocol_version": settings.protocol_version,
        "credentials_configured": auth_status.credentials_configured,
        "token_ready": auth_status.token_ready,
        "detail": auth_status.detail,
        "tools": sorted(tool["name"] for tool in TOOL_DEFINITIONS),
    }


def _authorize(authorization: str | None) -> None:
    expected = settings.bearer_token
    if not expected:
        return
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Missing bearer token.")
    provided = authorization.removeprefix("Bearer ").strip()
    if provided != expected:
        raise HTTPException(status_code=401, detail="Invalid bearer token.")


def _jsonrpc_result(request_id: int | str, result: dict[str, Any]) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": request_id, "result": result}


def _jsonrpc_error(
    request_id: int | str | None,
    *,
    code: int,
    message: str,
    data: dict[str, Any] | None = None,
) -> dict[str, Any]:
    error: dict[str, Any] = {"code": code, "message": message}
    if data is not None:
        error["data"] = data
    return {"jsonrpc": "2.0", "id": request_id, "error": error}


def _tool_by_name(name: str) -> dict[str, Any] | None:
    for tool in TOOL_DEFINITIONS:
        if tool["name"] == name:
            return tool
    return None


def _call_tool(name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    if name == "feishu_doc_create":
        return create_doc(arguments)
    if name == "feishu_doc_get":
        return get_doc(arguments)
    if name == "feishu_doc_read":
        return read_doc(arguments)
    if name == "feishu_doc_append":
        return not_implemented(name)
    if name == "feishu_doc_insert_after_heading":
        return not_implemented(name)
    if name == "feishu_doc_replace_text":
        return not_implemented(name)
    if name == "feishu_doc_delete_blocks":
        return not_implemented(name)
    raise FeishuDocServiceError(f"Unknown tool '{name}'.")


@app.post("/mcp")
async def mcp_endpoint(
    payload: dict[str, Any],
    response: Response,
    authorization: str | None = Header(default=None),
) -> dict[str, Any] | Response:
    _authorize(authorization)

    request_id = payload.get("id")
    method = str(payload.get("method") or "")
    params = payload.get("params")
    if params is None:
        params = {}
    if not isinstance(params, dict):
        return _jsonrpc_error(request_id, code=-32602, message="Invalid params.")

    if method == "initialize":
        return _jsonrpc_result(
            request_id,
            {
                "protocolVersion": settings.protocol_version,
                "capabilities": {
                    "tools": {
                        "listChanged": False,
                    }
                },
                "serverInfo": {
                    "name": settings.app_name,
                    "version": settings.app_version,
                },
                "instructions": "Feishu MCP server for Jarvis. Phase 1 currently exposes tool discovery and call skeletons.",
            },
        )

    if method == "notifications/initialized":
        response.status_code = 202
        return response

    if method == "tools/list":
        return _jsonrpc_result(request_id, {"tools": sorted(TOOL_DEFINITIONS, key=lambda item: str(item["name"]))})

    if method == "tools/call":
        name = str(params.get("name") or "").strip()
        if not name:
            return _jsonrpc_error(request_id, code=-32602, message="Missing tool name.")
        tool = _tool_by_name(name)
        if tool is None:
            return _jsonrpc_error(request_id, code=-32601, message=f"Unknown tool '{name}'.")
        arguments = params.get("arguments", {})
        if not isinstance(arguments, dict):
            return _jsonrpc_error(request_id, code=-32602, message="Tool arguments must be an object.")
        try:
            result = _call_tool(name, arguments)
            return _jsonrpc_result(
                request_id,
                {
                    "content": [
                        {
                            "type": "text",
                            "text": str(result),
                        }
                    ],
                    "structuredContent": result,
                    "isError": False,
                },
            )
        except FeishuDocServiceError as exc:
            return _jsonrpc_result(
                request_id,
                {
                    "content": [{"type": "text", "text": str(exc)}],
                    "isError": True,
                },
            )

    return _jsonrpc_error(request_id, code=-32601, message=f"Unknown method '{method}'.")

TOOL_DEFINITIONS.extend(
    [
        {
            "name": "feishu_doc_replace_text",
            "title": "Replace Text In Feishu Doc",
            "description": "Replace matched text in a bounded scope within a Feishu upgraded doc.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "document_id": {"type": "string"},
                    "document_url": {"type": "string"},
                    "find_text": {"type": "string"},
                    "replace_text": {"type": "string"},
                    "scope": {"type": "string"},
                    "heading_query": {"type": "string"},
                },
                "required": ["find_text", "replace_text", "scope"],
            },
        },
        {
            "name": "feishu_doc_delete_blocks",
            "title": "Delete Feishu Doc Blocks",
            "description": "Delete a server-resolved heading section or server-resolved block set.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "document_id": {"type": "string"},
                    "document_url": {"type": "string"},
                    "heading_query": {"type": "string"},
                    "block_refs": {"type": "array"},
                },
            },
        },
    ]
)

TOOL_DEFINITIONS.extend(
    [
        {
            "name": "feishu_doc_append",
            "title": "Append Feishu Doc Content",
            "description": "Append text-like blocks to the end of a Feishu upgraded doc.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "document_id": {"type": "string"},
                    "document_url": {"type": "string"},
                    "blocks": {"type": "array"},
                },
                "required": ["blocks"],
            },
        },
        {
            "name": "feishu_doc_insert_after_heading",
            "title": "Insert Content After Heading",
            "description": "Insert content after a matched heading inside a Feishu upgraded doc.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "document_id": {"type": "string"},
                    "document_url": {"type": "string"},
                    "heading_query": {"type": "string"},
                    "blocks": {"type": "array"},
                },
                "required": ["heading_query", "blocks"],
            },
        },
    ]
)
