from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass
from typing import Any, Literal

from app.core.config import settings
from app.mcp.client import MCPClientError, MCPHTTPClient

ToolSource = Literal["local", "mcp"]


@dataclass(frozen=True)
class ToolDefinition:
    name: str
    description: str
    input_schema: dict[str, Any]
    source: ToolSource
    server_name: str | None = None
    remote_name: str | None = None


@dataclass(frozen=True)
class ToolExecutionResult:
    status: str
    output: str
    remote_request_id: str | None = None
    payload: dict[str, Any] | None = None


@dataclass(frozen=True)
class MCPServerConfig:
    name: str
    base_url: str
    bearer_token: str
    timeout_ms: int


def local_tool_definitions(*, allow_subagent_tool: bool = True) -> list[ToolDefinition]:
    tools = [
        ToolDefinition(
            name="get_session_git_state",
            description="Read the current session's Git repository state, including repo root, lead branch, HEAD state, and working tree cleanliness.",
            input_schema={"type": "object", "properties": {}},
            source="local",
        ),
        ToolDefinition(
            name="list_files",
            description="List files in the current session workspace. You may optionally pass an explicit absolute directory path for a read-only external reference mentioned by the user.",
            input_schema={
                "type": "object",
                "properties": {"path": {"type": "string"}},
            },
            source="local",
        ),
        ToolDefinition(
            name="read_file",
            description="Read a file from the current session workspace. You may also read an explicit absolute path mentioned by the user as a read-only external reference.",
            input_schema={
                "type": "object",
                "properties": {"path": {"type": "string"}},
                "required": ["path"],
            },
            source="local",
        ),
        ToolDefinition(
            name="write_file",
            description="Create or overwrite a file in the current session workspace only. Do not use this for paths outside the current session workspace.",
            input_schema={
                "type": "object",
                "properties": {
                    "path": {"type": "string"},
                    "content": {"type": "string"},
                },
                "required": ["path", "content"],
            },
            source="local",
        ),
        ToolDefinition(
            name="edit_file",
            description="Replace exact text in a file in the current session workspace only. Do not use this for paths outside the current session workspace.",
            input_schema={
                "type": "object",
                "properties": {
                    "path": {"type": "string"},
                    "old_text": {"type": "string"},
                    "new_text": {"type": "string"},
                },
                "required": ["path", "old_text", "new_text"],
            },
            source="local",
        ),
        ToolDefinition(
            name="bash",
            description="Run a shell command in the current target workspace. This requires approval before execution.",
            input_schema={
                "type": "object",
                "properties": {"command": {"type": "string"}},
                "required": ["command"],
            },
            source="local",
        ),
        ToolDefinition(
            name="list_skills",
            description="List locally installed skills available to the agent.",
            input_schema={"type": "object", "properties": {}},
            source="local",
        ),
        ToolDefinition(
            name="load_skill",
            description="Load a local skill by name and read its SKILL.md instructions.",
            input_schema={
                "type": "object",
                "properties": {"name": {"type": "string"}},
                "required": ["name"],
            },
            source="local",
        ),
        ToolDefinition(
            name="memory_search",
            description="Search structured session memory for prior goals, constraints, decisions, progress, open questions, or artifact references in the current session.",
            input_schema={
                "type": "object",
                "properties": {
                    "query": {"type": "string"},
                    "kind": {"type": "string"},
                    "limit": {"type": "integer"},
                },
                "required": ["query"],
            },
            source="local",
        ),
        ToolDefinition(
            name="conversation_search",
            description="Search prior durable conversation messages from the current session when the current context pack is insufficient.",
            input_schema={
                "type": "object",
                "properties": {
                    "query": {"type": "string"},
                    "role": {"type": "string"},
                    "limit": {"type": "integer"},
                },
                "required": ["query"],
            },
            source="local",
        ),
        ToolDefinition(
            name="list_session_assets",
            description="List uploaded session attachments available in the current session, including their ids, names, types, and readiness.",
            input_schema={"type": "object", "properties": {}},
            source="local",
        ),
        ToolDefinition(
            name="read_asset_summary",
            description="Read a compact summary of one uploaded session attachment, including its status and representative extracted content if available.",
            input_schema={
                "type": "object",
                "properties": {
                    "asset_id": {"type": "string"},
                },
                "required": ["asset_id"],
            },
            source="local",
        ),
        ToolDefinition(
            name="search_asset_chunks",
            description="Search extracted chunks from one uploaded session attachment using a natural-language query.",
            input_schema={
                "type": "object",
                "properties": {
                    "asset_id": {"type": "string"},
                    "query": {"type": "string"},
                    "limit": {"type": "integer"},
                },
                "required": ["asset_id", "query"],
            },
            source="local",
        ),
        ToolDefinition(
            name="create_task",
            description="Create a lightweight task in the current session.",
            input_schema={
                "type": "object",
                "properties": {
                    "subject": {"type": "string"},
                    "description": {"type": "string"},
                },
                "required": ["subject"],
            },
            source="local",
        ),
        ToolDefinition(
            name="read_asset_chunk",
            description="Read one extracted chunk from an uploaded session attachment by chunk index.",
            input_schema={
                "type": "object",
                "properties": {
                    "asset_id": {"type": "string"},
                    "chunk_index": {"type": "integer"},
                },
                "required": ["asset_id", "chunk_index"],
            },
            source="local",
        ),
        ToolDefinition(
            name="generate_image",
            description="Generate a new image for the current session using a text prompt. Use this when the user asks you to create or render an image instead of only describing one.",
            input_schema={
                "type": "object",
                "properties": {
                    "prompt": {"type": "string"},
                    "asset_ids": {"type": "array", "items": {"type": "string"}},
                    "mask_asset_id": {"type": "string"},
                    "input_fidelity": {"type": "string"},
                    "size": {"type": "string"},
                    "background": {"type": "string"},
                    "quality": {"type": "string"},
                },
                "required": ["prompt"],
            },
            source="local",
        ),
        ToolDefinition(
            name="generate_speech",
            description="Generate spoken audio for the current session from a text prompt or answer. Use this when the user asks for audio output or wants the reply spoken aloud.",
            input_schema={
                "type": "object",
                "properties": {
                    "text": {"type": "string"},
                    "voice": {"type": "string"},
                    "format": {"type": "string"},
                    "speed": {"type": "number"},
                    "pitch": {"type": "number"},
                },
                "required": ["text"],
            },
            source="local",
        ),
        ToolDefinition(
            name="generate_video",
            description="Generate a video for the current session from a text prompt and optional source assets. Use this only when the user explicitly asks for video output.",
            input_schema={
                "type": "object",
                "properties": {
                    "prompt": {"type": "string"},
                    "asset_ids": {"type": "array", "items": {"type": "string"}},
                    "duration_seconds": {"type": "integer"},
                    "aspect_ratio": {"type": "string"},
                },
                "required": ["prompt"],
            },
            source="local",
        ),
    ]
    if allow_subagent_tool:
        tools.append(
            ToolDefinition(
                name="run_subagent",
                description="Delegate a bounded investigation or implementation subtask to a subagent. Use this for complex tasks, long investigations, or independent subproblems. The subagent returns a written summary of what it found or changed.",
                input_schema={
                    "type": "object",
                    "properties": {
                        "name": {"type": "string"},
                        "prompt": {"type": "string"},
                        "isolation_mode": {"type": "string", "enum": ["shared", "worktree"]},
                    },
                    "required": ["prompt"],
                },
                source="local",
            )
        )
    tools.extend(
        [
            ToolDefinition(
                name="create_teammate",
                description="Create a teammate agent for the current session.",
                input_schema={
                    "type": "object",
                    "properties": {
                        "name": {"type": "string"},
                        "role": {"type": "string"},
                    },
                    "required": ["name", "role"],
                },
                source="local",
            ),
            ToolDefinition(
                name="message_teammate",
                description="Send a message to a teammate agent in the current session.",
                input_schema={
                    "type": "object",
                    "properties": {
                        "agent_id": {"type": "integer"},
                        "content": {"type": "string"},
                    },
                    "required": ["agent_id", "content"],
                },
                source="local",
            ),
        ]
    )
    return tools


class ToolRegistry:
    def __init__(self) -> None:
        self._clients: dict[str, MCPHTTPClient] = {}
        self._cache: dict[bool, tuple[float, list[ToolDefinition]]] = {}

    async def list_tools(self, *, allow_subagent_tool: bool = True) -> list[ToolDefinition]:
        cached = self._cache.get(allow_subagent_tool)
        now = time.monotonic()
        if cached and now - cached[0] < settings.jarvis_mcp_cache_ttl_seconds:
            return cached[1]

        local_tools = local_tool_definitions(allow_subagent_tool=allow_subagent_tool)
        merged = list(local_tools)
        used_names = {tool.name for tool in merged}
        for server in self._server_configs():
            try:
                remote_tools = await asyncio.to_thread(self._client_for, server)
                listed = await asyncio.to_thread(remote_tools.list_tools)
            except MCPClientError:
                continue
            for remote_tool in listed:
                name = self._dedupe_name(remote_tool.name, server.name, used_names)
                used_names.add(name)
                merged.append(
                    ToolDefinition(
                        name=name,
                        description=remote_tool.description,
                        input_schema=remote_tool.input_schema,
                        source="mcp",
                        server_name=server.name,
                        remote_name=remote_tool.name,
                    )
                )
        self._cache[allow_subagent_tool] = (now, merged)
        return merged

    async def call_tool(self, tool: ToolDefinition, arguments: dict[str, Any]) -> ToolExecutionResult:
        if tool.source != "mcp" or not tool.server_name or not tool.remote_name:
            raise MCPClientError("Tool is not configured as an MCP-backed tool.")
        server = self._server_configs_by_name().get(tool.server_name)
        if not server:
            raise MCPClientError(f"Unknown MCP server '{tool.server_name}'.")
        client = self._client_for(server)
        result = await asyncio.to_thread(client.call_tool, tool.remote_name, arguments)
        status = "error" if result.is_error else "completed"
        return ToolExecutionResult(
            status=status,
            output=result.text,
            remote_request_id=result.request_id,
        )

    def _server_configs(self) -> list[MCPServerConfig]:
        servers: list[MCPServerConfig] = []
        if settings.jarvis_mcp_feishu_enabled and settings.jarvis_mcp_feishu_base_url:
            servers.append(
                MCPServerConfig(
                    name="feishu",
                    base_url=settings.jarvis_mcp_feishu_base_url,
                    bearer_token=settings.jarvis_mcp_feishu_bearer_token,
                    timeout_ms=settings.jarvis_mcp_feishu_timeout_ms,
                )
            )
        return servers

    def _server_configs_by_name(self) -> dict[str, MCPServerConfig]:
        return {server.name: server for server in self._server_configs()}

    def _client_for(self, server: MCPServerConfig) -> MCPHTTPClient:
        client = self._clients.get(server.name)
        if client is None:
            client = MCPHTTPClient(
                base_url=server.base_url,
                bearer_token=server.bearer_token,
                timeout_ms=server.timeout_ms,
            )
            self._clients[server.name] = client
        return client

    def _dedupe_name(self, name: str, server_name: str, used_names: set[str]) -> str:
        if name not in used_names:
            return name
        prefixed = f"{server_name}.{name}"
        if prefixed not in used_names:
            return prefixed
        index = 2
        while True:
            candidate = f"{server_name}.{name}.{index}"
            if candidate not in used_names:
                return candidate
            index += 1


tool_registry = ToolRegistry()
