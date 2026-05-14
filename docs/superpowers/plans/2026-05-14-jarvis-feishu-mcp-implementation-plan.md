# Jarvis Feishu MCP Implementation Plan

Date: 2026-05-14
Depends on: `docs/superpowers/specs/2026-05-14-jarvis-feishu-mcp-design.md`

## Delivery Strategy

Implement the Feishu MCP integration in four phases:

1. Jarvis MCP client and tool-registry refactor
2. Feishu MCP server skeleton and auth plumbing
3. Feishu Docs tool workflows
4. End-to-end integration and verification

The goal is to add remote Feishu document capabilities without bloating the Jarvis runtime with SaaS-specific logic.

## Phase 1: Jarvis MCP Client And Tool Registry

### Objectives

- Add an HTTP MCP client boundary to the Jarvis backend
- Stop hardcoding all tool schemas directly in the runtime manager
- Allow local and MCP-backed tools to coexist behind one execution path

### Tasks

- Add `backend/app/mcp/client.py` for MCP tool discovery and invocation
- Add `backend/app/mcp/transport_http.py` for HTTP request handling, auth headers, and response normalization
- Add `backend/app/mcp/registry.py` to merge:
  - local tools
  - MCP tools from configured servers
- Refactor `backend/app/runtime/manager.py` so the agent loop:
  - obtains tools from the merged registry
  - routes execution through one `execute_tool()` path
- Extend tool execution persistence to record:
  - `tool_source`
  - `server_name`
  - `latency_ms`
  - `remote_request_id`
- Add config wiring for:
  - `JARVIS_MCP_FEISHU_ENABLED`
  - `JARVIS_MCP_FEISHU_BASE_URL`
  - `JARVIS_MCP_FEISHU_BEARER_TOKEN`
  - `JARVIS_MCP_FEISHU_TIMEOUT_MS`

### Exit Criteria

- Jarvis can fetch tool definitions from a configured HTTP MCP server
- Local and remote tools appear in one model-facing tool list
- Remote tool calls are persisted in the same execution history flow as local tools

## Phase 2: Feishu MCP Server Skeleton And Auth

### Objectives

- Stand up a dedicated Feishu MCP server over HTTP
- Isolate Feishu credentials and token lifecycle from Jarvis
- Establish the shared server-side modules needed for later document workflows

### Tasks

- Create a standalone Feishu MCP server project or service directory
- Add `auth.py` for:
  - `app_id` / `app_secret` loading
  - `tenant_access_token` fetch
  - token caching
  - pre-expiry refresh
- Add `feishu_client.py` for:
  - base HTTP client
  - common headers
  - error mapping
- Add `mcp_server.py` with:
  - HTTP transport setup
  - server metadata
  - tool registration
- Add basic health and config validation checks
- Add server config wiring for:
  - `FEISHU_APP_ID`
  - `FEISHU_APP_SECRET`
  - `FEISHU_MCP_BEARER_TOKEN`
  - `FEISHU_API_BASE_URL`
  - `FEISHU_TOKEN_REFRESH_SKEW_SECONDS`
  - `FEISHU_MAX_RETRIES`

### Exit Criteria

- Jarvis can connect to the Feishu MCP server over HTTP
- The server can authenticate to Feishu and obtain a usable access token
- Basic MCP tool listing works without exposing any document mutations yet

## Phase 3: Feishu Docs Tool Workflows

### Objectives

- Implement the Phase-1 Feishu Docs tool set
- Encapsulate block traversal and block mutation inside server-side services
- Add safety checks for replace and delete workflows

### Tasks

- Add `doc_parser.py` for:
  - block-tree traversal
  - linearized plain-text rendering
  - heading extraction
  - stable server-generated block references
- Add `doc_service.py` for the following workflows:
  - `feishu_doc_create`
  - `feishu_doc_get`
  - `feishu_doc_read`
  - `feishu_doc_append`
  - `feishu_doc_insert_after_heading`
  - `feishu_doc_replace_text`
  - `feishu_doc_delete_blocks`
- Add `rate_limit.py` for:
  - per-document write serialization
  - bounded retries
  - exponential backoff
- Implement heading matching rules:
  - exact match
  - normalized match
  - explicit ambiguity failure
- Implement replace and delete preview rules:
  - match count threshold
  - `needs_confirmation`
  - preview payloads
- Add structured logging for every mutating tool call:
  - document identifier
  - input summary
  - pre-write revision
  - post-write revision

### Exit Criteria

- The Feishu MCP server exposes all seven Phase-1 tools
- Read results return a stable linearized document view
- Replace and delete operations enforce bounded, inspectable mutations

## Phase 4: End-To-End Integration And Verification

### Objectives

- Verify Jarvis can use Feishu tools through MCP in realistic agent turns
- Confirm permission errors, safety checks, and auditability work as designed
- Lock the integration down with build and runtime checks

### Tasks

- Wire Jarvis config to enable the Feishu MCP server in development
- Verify discovery and invocation from the Jarvis agent loop
- Run the core acceptance flows:
  - create a new doc with initial content
  - read the created doc
  - read an existing doc explicitly authorized to the app
  - insert content after a matched heading
  - replace the first occurrence of a target phrase
  - delete one heading section
  - attempt access to a non-authorized doc and confirm a permission error
- Verify execution history includes remote tool metadata
- Run backend syntax or test checks for Jarvis and the Feishu MCP server
- Document the operator setup steps:
  - create Feishu custom app
  - add required API scopes
  - grant document access to the app
  - configure Jarvis and MCP server environment variables

### Exit Criteria

- Jarvis can complete the core Feishu document workflows through MCP
- Unauthorized existing docs fail cleanly with actionable errors
- Remote tool calls are observable in logs and execution history
- Setup steps are documented well enough to reproduce the integration locally

## Sequencing Notes

- Phase 1 should land before any real Feishu document tool work, otherwise remote tools will be bolted onto the old static runtime path
- Phase 2 can be developed in parallel with small parts of Phase 1, but Phase 3 depends on the auth and transport skeleton being stable
- Phase 4 should not begin until the full seven-tool surface is implemented

## Risks To Watch During Implementation

- Letting Phase 1 turn into a full runtime rewrite instead of a bounded tool-registry refactor
- Exposing raw Feishu block identifiers too early and forcing the model into low-level editing behavior
- Under-specifying heading matching and producing edits in the wrong section
- Missing idempotency or retry guards on document writes
- Treating Feishu API scope as sufficient without validating document-level authorization
