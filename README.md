# Jarvis

Local single-user agent cockpit inspired by `learn-claude-code/agents/s_full.py`.

## Structure

- `frontend/`: React + TypeScript cockpit UI
- `backend/`: Python runtime, API, tools, and agent orchestration
- `services/feishu_mcp_server/`: standalone HTTP MCP server for Feishu Docs
- `src-tauri/`: Tauri desktop shell
- `docs/superpowers/`: design and implementation planning artifacts

## Current State

This repository now contains the initial scaffold for:

- Tauri desktop packaging
- a cockpit-style frontend shell
- a Python backend skeleton with session, event, and agent runtime boundaries

The project still needs dependency installation before it can be run locally.

## Local Run

1. Install backend dependencies:
   `./.venv/bin/python -m pip install -e backend`
2. Install frontend and Tauri CLI dependencies:
   `cd frontend && npm install`
3. Start the desktop app:
   `cd frontend && npm run tauri:dev`

The Tauri shell now auto-starts the Python backend if `127.0.0.1:8731` is not already in use.

## Feishu MCP Local Prep

For the Feishu Docs MCP integration, there are two local processes:

1. Jarvis backend
2. Feishu MCP server

### Install

```bash
./.venv/bin/python -m pip install -e backend
./.venv/bin/python -m pip install -e services/feishu_mcp_server
```

### Configure

- Backend sample env: [backend/.env.example](/Users/bytedance/Desktop/python/Jarvis/backend/.env.example:1)
- Feishu MCP sample env: [services/feishu_mcp_server/.env.example](/Users/bytedance/Desktop/python/Jarvis/services/feishu_mcp_server/.env.example:1)

Recommended local setup:

1. Copy `backend/.env.example` to `backend/.env`
2. Copy `services/feishu_mcp_server/.env.example` to `services/feishu_mcp_server/.env`
3. Put the same shared bearer token on both sides
4. Put your Feishu `app_id` and `app_secret` only in `services/feishu_mcp_server/.env`

The backend and Feishu MCP server now auto-load local `.env` files, so you do not need to export secrets manually or send them in chat.

### Run The Feishu MCP Server

```bash
./.venv/bin/python -m feishu_mcp_server.main
```

By default it serves:

- `GET http://127.0.0.1:8765/health`
- `POST http://127.0.0.1:8765/mcp`

### Smoke Test The MCP Server

```bash
./.venv/bin/python services/feishu_mcp_server/scripts/smoke_mcp_http.py \
  --base-url http://127.0.0.1:8765/mcp \
  --token replace-with-same-shared-secret \
  tools
```

More detail is in [services/feishu_mcp_server/README.md](/Users/bytedance/Desktop/python/Jarvis/services/feishu_mcp_server/README.md:1).

## Postgres Concurrency Tests

The backend now includes Postgres-only concurrency tests for lease and approval races in [backend/tests/test_postgres_concurrency.py](/Users/bytedance/Desktop/python/Jarvis/backend/tests/test_postgres_concurrency.py:1).

### Run With Docker

Use the helper script:

```bash
bash backend/scripts/run_postgres_concurrency_tests.sh
```

The script will:

1. Start a temporary Postgres 16 container from [backend/docker-compose.postgres-test.yml](/Users/bytedance/Desktop/python/Jarvis/backend/docker-compose.postgres-test.yml:1)
2. Wait for the database to become ready
3. Export `JARVIS_TEST_POSTGRES_URL`
4. Run `backend/tests/test_postgres_concurrency.py`
5. Tear the container down automatically

### Run Against An Existing Postgres Instance

If you already have Postgres available, set `JARVIS_TEST_POSTGRES_URL` and run:

```bash
PYTHONPATH=backend \
JARVIS_TEST_POSTGRES_URL='postgresql+psycopg://postgres:postgres@127.0.0.1:55432/postgres' \
python3 -m pytest backend/tests/test_postgres_concurrency.py
```

When `JARVIS_TEST_POSTGRES_URL` is unset, the Postgres-only tests are skipped by default.
