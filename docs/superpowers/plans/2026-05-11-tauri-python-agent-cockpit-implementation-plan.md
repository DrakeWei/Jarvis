# Tauri Python Agent Cockpit Implementation Plan

Date: 2026-05-11
Depends on: `docs/superpowers/specs/2026-05-11-tauri-python-agent-cockpit-design.md`

## Delivery Strategy

Implement `v1` as a modular monolith in five phases:

1. Project bootstrap and process wiring
2. Backend core and persistence
3. Agent runtime and tool execution
4. Frontend cockpit UI
5. Integration, recovery, and verification

The goal is to reach a usable local desktop application early, then fill in higher-level agent capabilities on top of a stable runtime and data layer.

## Phase 1: Project Bootstrap

### Objectives

- Create the repository structure from the design doc
- Initialize `frontend/`, `backend/`, and `src-tauri/`
- Establish local development commands
- Ensure `Tauri` can launch the frontend and manage the Python backend

### Tasks

- Create frontend app with `React + TypeScript + Vite`
- Create Tauri shell with local dev and build config
- Create backend package layout with `FastAPI` entrypoint
- Add root documentation and local run instructions
- Add `.gitignore` entries for runtime data, frontend build output, Python caches, and `.superpowers/`

### Exit Criteria

- `frontend` starts locally
- `backend` starts locally
- `Tauri` window opens and can reach the frontend
- Tauri can launch or connect to the backend in development mode

## Phase 2: Backend Core And Persistence

### Objectives

- Establish the backend application structure
- Create the database schema and session lifecycle
- Add event delivery primitives

### Tasks

- Add backend config and settings management
- Add `SQLAlchemy` engine, session factory, and migrations strategy
- Create initial models:
  - `sessions`
  - `messages`
  - `turns`
  - `tool_executions`
  - `tasks`
  - `task_dependencies`
  - `agents`
  - `agent_messages`
  - `approvals`
  - `background_jobs`
  - `event_log`
- Create service layer for session creation, message append, and event append
- Expose initial `HTTP` and `WebSocket` endpoints

### Exit Criteria

- A session can be created and reloaded from `SQLite`
- Messages persist and can be queried
- The frontend can subscribe to a session event stream

## Phase 3: Agent Runtime And Tools

### Objectives

- Build the core lead-agent turn loop
- Implement tool execution with auditability
- Support subagents, teammates, tasks, approvals, and background jobs

### Tasks

- Add provider abstraction for model calls
- Implement lead session runtime with streaming output
- Implement tool broker for `bash`, `read_file`, `write_file`, and `edit_file`
- Add timeout, truncation, and structured result handling
- Add approval guard for sensitive actions
- Implement background job runner with persisted state
- Implement task service with create, update, claim, and dependencies
- Implement subagent runner with isolated context and summarized output
- Implement teammate manager with inbox, role, status, and manual messaging
- Add basic compaction path for long sessions

### Exit Criteria

- A GUI message can drive a full lead-agent turn
- Tool calls are visible as persisted execution records
- Background jobs complete and emit events
- Tasks, subagents, teammates, and approvals work end-to-end

## Phase 4: Frontend Cockpit UI

### Objectives

- Implement the three-column cockpit
- Make runtime state understandable without reading raw logs

### Tasks

- Build session/workspace left column
- Build conversation timeline center column
- Build right-side tab panels for `Tasks`, `Teammates`, `Approvals`, and `Logs`
- Add streaming message rendering
- Add tool execution detail views
- Add controls for send, stop, create task, spawn subagent, spawn teammate, send teammate message, and approval actions
- Add state badges for `thinking`, `tool_running`, `waiting_approval`, `background_running`, `completed`, and `cancelled`

### Exit Criteria

- A user can operate the full `v1` workflow from the GUI
- Structured system events are legible in the timeline and side panels

## Phase 5: Integration, Recovery, And Verification

### Objectives

- Make the app robust enough for repeated local use
- Verify persistence and recovery behavior

### Tasks

- Add startup checks and backend health handling
- Add session reload and reconnect flow
- Add database initialization and first-run behavior
- Add logging and error surfaces for failed tools and failed model calls
- Add automated tests:
  - unit tests for tools, tasks, teammate messaging, and approvals
  - integration tests for session runtime and event delivery
  - frontend tests for cockpit panels
  - end-to-end smoke test for local round-trip

### Exit Criteria

- App starts cleanly from a fresh checkout
- Existing session state can be reopened
- Failure states are visible and recoverable
- The core `v1` smoke test passes

## Suggested Implementation Order

1. Bootstrap repo structure and dev tooling
2. Build backend config, database, and event API
3. Add session runtime and tool broker
4. Add tasks and background jobs
5. Add subagents, teammates, and approvals
6. Build frontend cockpit against the live backend
7. Harden recovery, logging, and tests

## Files And Areas Expected To Be Created

- `frontend/`
- `src-tauri/`
- `backend/app/api/`
- `backend/app/core/`
- `backend/app/db/`
- `backend/app/models/`
- `backend/app/providers/`
- `backend/app/runtime/`
- `backend/app/schemas/`
- `backend/app/services/`
- `backend/app/storage/`
- `backend/app/tools/`
- `backend/tests/`
- root `.gitignore`
- updated root `main.py` or replacement entry documentation, depending on bootstrap strategy

## Risks To Manage

- Tauri-to-backend lifecycle management during development
- Keeping the Rust layer thin instead of leaking app logic into it
- Preventing tool execution from blocking the event loop
- Keeping teammate and background-job state consistent across reconnects
- Avoiding a chat-only UI that hides structured agent state

## Definition Of Done For V1

`v1` is done when a user can open the desktop app, create or resume a session, send a message, observe streamed output, watch tool execution, create and track tasks, run subagents, operate teammates, handle approvals, and recover state after restart without relying on the terminal.
