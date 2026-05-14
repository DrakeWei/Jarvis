# Jarvis Feishu MCP Integration Design

Date: 2026-05-14
Status: Approved in conversation, spec written for review

## Summary

Add MCP-based Feishu Docs capabilities to Jarvis so the agent can create, read, append to, update, and delete content in Feishu upgraded docs through a dedicated remote MCP server.

The first version should keep Jarvis as the MCP client and move all Feishu-specific logic into a standalone `Feishu MCP Server` that Jarvis calls over HTTP. The server should use Feishu custom-app credentials with application identity, support newly created docs plus existing docs explicitly authorized to the app, and expose a small set of mid-level document tools rather than raw block APIs.

## Goals

- Let Jarvis use MCP to operate Feishu upgraded docs
- Keep Feishu integration outside the Jarvis runtime core
- Use application identity for Feishu operations
- Support docs created by the app and existing docs explicitly granted to the app
- Expose model-friendly document tools instead of raw block primitives
- Preserve clear security boundaries, rate limiting, and auditability
- Fit the current Jarvis backend architecture with minimal frontend impact

## Non-Goals

- Supporting Sheets, Bitable, or Wiki in this phase
- Supporting user-identity auth flows in this phase
- Exposing every Feishu raw document or permission API directly to the model
- Full MCP OAuth authorization between Jarvis and the Feishu MCP server in this phase
- Multi-tenant server design
- A generic plugin marketplace or dynamic server installation flow

## Approved Approach

Use a dedicated remote MCP server for Feishu and integrate it into Jarvis as an additional tool source.

Recommended runtime chain:

`Jarvis Runtime -> HTTP MCP Client -> Feishu MCP Server -> Feishu OpenAPI`

This design keeps Jarvis focused on agent orchestration and tool selection while moving Feishu auth, block parsing, request shaping, retry logic, and permission handling into one isolated service boundary.

## Why This Shape

Jarvis currently hardcodes tool schemas in the runtime and executes local tools directly through a local tool broker. That works for filesystem and shell operations, but it is the wrong boundary for a third-party SaaS integration.

If Feishu logic is placed directly into the Jarvis runtime, future SaaS integrations will continue to bloat the runtime and tool broker. An MCP client boundary avoids that by turning remote business capabilities into discoverable tools.

The Feishu side should still avoid exposing raw block-level APIs directly to the model. The Feishu MCP server should translate higher-level editing requests into the necessary Feishu document and block API calls.

## Phase-1 Scope

### Supported Resource

- Feishu upgraded docs (`docx`) only

### Supported Document Sources

- Docs created by the app
- Existing docs explicitly authorized to the app

### Supported Editing Level

- Mid-level editing only
- Create doc
- Read doc content
- Append content to the end
- Insert content after a heading
- Replace matched text
- Delete a selected section or selected blocks identified by the server

### Deferred

- User delegated auth
- Generic raw block editing tools
- Full-text overwrite for long documents
- Broad document discovery across the whole tenant
- Rich media insertion as a first-class workflow
- Permission management exposed to the model

## Feishu Capability Model

Feishu upgraded docs are tree-structured documents made of blocks. Each document has a `document_id`, and the page root block uses the same identifier as the document. Phase 1 should treat this as an implementation detail of the Feishu MCP server, not as a model-facing interface.

The MCP tools should expose a simpler document model to Jarvis:

- document identity
- document metadata
- linearized text view
- heading index
- stable server-generated references for matched headings or block groups

This lets the model ask for document operations in business terms while the server handles block lookup and mutation internally.

## MCP Surface

Phase 1 should focus on MCP `tools`.

Do not make `resources` or `prompts` central to the first version. Jarvis already has a model-driven tool loop, while it does not yet have a mature resource-assembly path. Tool-first integration is the lowest-risk fit for the current runtime.

### Phase-1 Tool Set

#### `feishu_doc_create`

Purpose:
- Create a new Feishu upgraded doc
- Optionally place it in a target folder
- Optionally seed it with initial content

Required input:
- `title`

Optional input:
- `folder_token`
- `initial_blocks`

Returns:
- `document_id`
- `url`
- `title`
- `root_block_id`
- `revision_id`

#### `feishu_doc_get`

Purpose:
- Resolve document identity and fetch document metadata

Input:
- `document_id` or `document_url`

Returns:
- `document_id`
- `title`
- `revision_id`
- `can_read`
- `can_edit`
- `url`

#### `feishu_doc_read`

Purpose:
- Read a document and return an LLM-friendly structure

Input:
- `document_id` or `document_url`
- optional pagination or truncation controls

Returns:
- `plain_text`
- `headings`
- `blocks`
- `revision_id`
- `truncated`

The server should hide raw tree traversal from Jarvis and return a stable linearized representation.

#### `feishu_doc_append`

Purpose:
- Append text-like blocks at the end of the document

Input:
- `document_id` or `document_url`
- `blocks`

Returns:
- `inserted_blocks`
- `revision_id`

#### `feishu_doc_insert_after_heading`

Purpose:
- Locate a heading by text and insert content immediately after that section heading

Input:
- `document_id` or `document_url`
- `heading_query`
- `blocks`
- optional match strategy

Returns:
- `matched_heading`
- `inserted_blocks`
- `revision_id`

This tool is intentionally higher level than raw block insertion because it maps more closely to how users phrase document edits.

#### `feishu_doc_replace_text`

Purpose:
- Replace matched text inside one or more text-bearing blocks

Input:
- `document_id` or `document_url`
- `find_text`
- `replace_text`
- `scope`
- optional `heading_query`

Returns:
- `match_count`
- `updated_blocks`
- `revision_id`
- optional `needs_confirmation`
- optional `preview`

The server should use block-aware updates rather than full document overwrite.

#### `feishu_doc_delete_blocks`

Purpose:
- Delete a server-resolved section or a server-resolved list of blocks

Input:
- `document_id` or `document_url`
- either `heading_query` or `block_refs`

Returns:
- `deleted_count`
- `revision_id`
- optional `needs_confirmation`
- optional `preview`

The model should not be allowed to pass arbitrary raw `block_id` values as the main public interface.

## Authentication And Authorization

### Jarvis To MCP Server

Phase 1 should use a simple internal trust model:

- static bearer token, or
- network allowlist, or
- both

Do not block the project on full MCP OAuth. The transport is HTTP, and the deployment is a controlled single-user or single-team environment.

### MCP Server To Feishu

Use Feishu custom-app credentials:

- `app_id`
- `app_secret`

The Feishu MCP server should exchange these for `tenant_access_token`, cache the token, and refresh it before expiry. Jarvis should never handle Feishu credentials directly.

### Feishu Permission Boundary

The design must account for two distinct Feishu permission layers:

- app API scope
- document-level authorization

Even when the app has the correct API scopes, an existing document still must be explicitly shared with or otherwise granted to the app before the app can operate on it.

## Safety Model

The Feishu MCP server should enforce additional write protections beyond normal tool schemas.

### Replace Operations

- return a preview before large replacements
- set `needs_confirmation` when the match count exceeds a threshold
- prefer precise scopes such as `first` or `heading_scoped`

### Delete Operations

- require server-side resolution of a heading section or prior returned block references
- reject arbitrary raw block IDs as the primary public path
- set `needs_confirmation` when the deletion range is large

### Write Logging

Every mutating operation should log:

- tool name
- document identifier
- summarized inputs
- preview or match information
- pre-write revision
- post-write revision
- timestamp

## Rate Limiting And Concurrency

The Feishu MCP server must treat document writes as a controlled resource.

Required behaviors:

- serialize writes per `document_id`
- use bounded retries with exponential backoff on retryable rate-limit failures
- avoid optimistic parallel writes to the same document
- surface clear retryable vs non-retryable errors back to Jarvis

Read operations may be concurrent, but write operations should be serialized at the document level.

## Jarvis Backend Changes

Jarvis should not keep growing the static tool list inside the runtime manager. Instead, it should move to a merged tool registry model.

### Recommended New Modules

- `backend/app/mcp/client.py`
- `backend/app/mcp/transport_http.py`
- `backend/app/mcp/registry.py`

### Runtime Refactor

Replace the current shape:

- runtime defines all tool schemas directly
- runtime executes local tools directly

With the new shape:

- local tool registry
- MCP tool registry
- unified `execute_tool()` path

### Tool Source Model

Each tool definition should include:

- `name`
- `description`
- `input_schema`
- `source`
- optional `server_name`
- optional safety metadata

`source` should be one of:

- `local`
- `mcp`

### Tool Execution Model

For `local` tools:
- continue routing to the existing local broker path

For `mcp` tools:
- call the configured MCP client
- normalize the tool result into the same model-facing structure used for local tools

### Persistence Changes

Extend tool execution records to include:

- `tool_source`
- `server_name`
- `latency_ms`
- `remote_request_id`

This keeps local and remote tool activity observable in the same UI and audit flow.

## Feishu MCP Server Internal Modules

Recommended server modules:

- `auth.py`
- `feishu_client.py`
- `doc_parser.py`
- `doc_service.py`
- `rate_limit.py`
- `mcp_server.py`

Responsibilities:

- `auth.py`
  token exchange, caching, refresh
- `feishu_client.py`
  typed Feishu API wrapper and error mapping
- `doc_parser.py`
  convert Feishu block trees to linearized document views and back to write requests
- `doc_service.py`
  high-level document workflows used by MCP tools
- `rate_limit.py`
  per-document serialization and retry policy
- `mcp_server.py`
  MCP transport, tool registration, request validation, response formatting

## Configuration

### Jarvis

Suggested settings:

- `JARVIS_MCP_FEISHU_ENABLED`
- `JARVIS_MCP_FEISHU_BASE_URL`
- `JARVIS_MCP_FEISHU_BEARER_TOKEN`
- `JARVIS_MCP_FEISHU_TIMEOUT_MS`

### Feishu MCP Server

Suggested settings:

- `FEISHU_APP_ID`
- `FEISHU_APP_SECRET`
- `FEISHU_MCP_BEARER_TOKEN`
- `FEISHU_API_BASE_URL`
- `FEISHU_TOKEN_REFRESH_SKEW_SECONDS`
- `FEISHU_WRITE_CONFIRM_THRESHOLD`
- `FEISHU_MAX_RETRIES`

## Error Handling

Error messages returned from the Feishu MCP server should be explicit and actionable.

Minimum categories:

- configuration error
- authentication failure
- document permission denied
- document not found
- ambiguous heading match
- no text match found
- write confirmation required
- rate limit hit and retries exhausted
- Feishu upstream error

Jarvis should pass these back to the model in a concise form so the model can recover when possible.

## Acceptance Criteria

- Jarvis can discover and call Feishu document tools through MCP over HTTP
- Jarvis can create a new Feishu upgraded doc through `feishu_doc_create`
- Jarvis can read an authorized existing doc through `feishu_doc_read`
- Jarvis can insert content after a matched heading
- Jarvis can replace text in a bounded scope without full-document overwrite
- Jarvis can delete a server-resolved section with safety checks
- Feishu credentials remain isolated to the Feishu MCP server
- Existing docs that are not authorized to the app fail with clear permission errors
- Remote tool executions are visible in Jarvis execution history

## Phase-1 Test Scenarios

1. Create a new document with a title and initial content.
2. Read the created document and verify returned headings and plain text.
3. Read an existing document that has been explicitly granted to the app.
4. Insert a paragraph under a matched heading.
5. Replace the first occurrence of a target phrase.
6. Delete one heading section and verify the resulting revision changes.
7. Attempt to access a non-authorized existing doc and verify a permission error.

## Risks To Manage

- Jarvis runtime complexity continues to grow if MCP tools are bolted onto the old static schema path
- Ambiguous heading matching causes edits in the wrong section
- Large replacements or deletions happen without enough preview or confirmation
- Feishu document permissions are misunderstood as equivalent to API scope
- Write retries create duplicate edits if idempotency is not handled carefully
- Document linearization loses enough structure that the model makes poor edit decisions

## Open Follow-Up For Phase 2

- Add support for user-delegated auth where "my account" must mean a specific user identity
- Expose selected document resources or templates through MCP resources or prompts
- Add richer content blocks such as tables, images, and callouts
- Add document search or controlled discovery across allowed spaces
- Extend the same MCP pattern to Sheets and Wiki
