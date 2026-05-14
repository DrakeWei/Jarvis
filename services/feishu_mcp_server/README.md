# Feishu MCP Server

Standalone HTTP MCP server for Jarvis. This service owns:

- Feishu app credentials
- `tenant_access_token` refresh
- Feishu Docs tool exposure over MCP

## Install

From repo root:

```bash
python3 -m venv .venv
./.venv/bin/python -m pip install -e backend
./.venv/bin/python -m pip install -e services/feishu_mcp_server
```

## Configure

1. Copy [`.env.example`](/Users/bytedance/Desktop/python/Jarvis/services/feishu_mcp_server/.env.example:1) to `services/feishu_mcp_server/.env`.
2. Fill `FEISHU_APP_ID` and `FEISHU_APP_SECRET` from your Feishu custom app.
3. Fill `FEISHU_MCP_BEARER_TOKEN` with a local shared secret.
4. Give the Feishu app access to target docs before testing existing documents.

You do not need to paste these secrets into chat. The server now auto-loads:

- repo root `.env`
- `services/feishu_mcp_server/.env`

If your company network injects a self-signed or enterprise TLS certificate, also set:

- `FEISHU_CA_BUNDLE=/absolute/path/to/your/ca-bundle.pem`

If `FEISHU_CA_BUNDLE` is empty, the server will try `SSL_CERT_FILE`, then `certifi`.

Recommended minimum Feishu scopes for the current implementation:

- create and edit upgraded docs
- view upgraded docs
- convert text content into doc blocks
- document sharing / collaborator permission management if you want auto-share after create

Optional auto-share after create:

- `FEISHU_DEFAULT_EDITOR_MEMBER_TYPE=openid`
- `FEISHU_DEFAULT_EDITOR_MEMBER_ID=你的open_id`
- `FEISHU_DEFAULT_EDITOR_PERM=edit`

If these are set, `feishu_doc_create` will try to add you as a collaborator right after creating the doc. You can also pass `share_with` explicitly when calling the tool.

## Run

```bash
./.venv/bin/python -m feishu_mcp_server.main
```

The service listens on `127.0.0.1:8765` by default and serves:

- `GET /health`
- `POST /mcp`

## Smoke Test

Run the local MCP smoke script:

```bash
./.venv/bin/python services/feishu_mcp_server/scripts/smoke_mcp_http.py \
  --base-url http://127.0.0.1:8765/mcp \
  --token replace-with-local-shared-secret \
  tools
```

Useful smoke commands:

- `health`
- `tools`
- `call --tool feishu_doc_get --args '{"document_id":"doxc..."}'`
- `call --tool feishu_doc_read --args '{"document_id":"doxc...","max_blocks":50}'`

## Current State

Implemented:

- MCP initialize flow
- MCP tool listing
- Feishu auth status health check
- `feishu_doc_create`
- `feishu_doc_get`
- `feishu_doc_read`
- experimental `feishu_doc_append`
- experimental `feishu_doc_insert_after_heading`

Preview-only for safety:

- `feishu_doc_replace_text`
- `feishu_doc_delete_blocks`

The two experimental write flows still need runtime verification against a real Feishu tenant before they should be treated as stable.
