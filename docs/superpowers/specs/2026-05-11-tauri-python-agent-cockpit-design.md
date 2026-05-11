# Tauri Python Agent Cockpit Design

Date: 2026-05-11
Status: Approved in conversation, pending implementation planning

## Summary

Build a local single-user desktop agent application inspired by `/Users/bytedance/Desktop/python/learn-claude-code/agents/s_full.py`, but implemented as a modular monolith with a GUI. The app will use `Tauri` as the desktop shell, a web frontend for the cockpit UI, and a Python backend for agent execution, tools, persistence, and real-time events.

The long-term direction is a general multi-agent assistant platform. The first version is a local coding-agent cockpit.

## Goals

- Preserve the core mechanisms of `s_full.py`
- Replace the REPL with a GUI
- Keep `v1` local-only and single-user
- Cleanly separate UI, runtime, tools, and persistence
- Support chat, tasks, subagents, teammates, approvals, background jobs, and execution logs
- Make state durable, inspectable, and recoverable

## Non-Goals

- Multi-user collaboration
- Remote hosting and cloud sync
- Plugin marketplace or dynamic plugin install
- Heavy memory and retrieval systems
- Full IDE replacement features

## Architecture

### Layering

1. `Tauri Shell`
   Owns desktop windowing, packaging, menus, tray behavior, and Python backend lifecycle.
2. `Web Frontend`
   Owns the GUI: session list, conversation timeline, tasks, teammates, approvals, and logs.
3. `Python Backend`
   Owns agent runtime, tool dispatch, session orchestration, background work, APIs, and events.
4. `SQLite + Workspace Files`
   `SQLite` is the primary system of record. Filesystem storage is used for workspace outputs, exports, attachments, and caches.

### Backend Modules

- `Session Engine`
  Runs lead-agent turns, manages streaming output, handles cancellation, and applies compaction.
- `Tool Broker`
  Executes `bash`, `read_file`, `write_file`, and `edit_file` behind a consistent interface with timeout and permission checks.
- `Agent Runtime`
  Manages lead agent, subagent runs, teammate agents, inbox messaging, task claims, and approval workflows.
- `Event Gateway`
  Exposes local `HTTP + WebSocket` endpoints so the frontend can send commands and receive live state updates.

## Runtime Model

`v1` uses a single Python backend process with `asyncio` task scheduling.

- A `lead session` maps to one primary conversation runtime.
- A `subagent run` is a bounded child execution with isolated context and a summarized return value.
- A `teammate agent` is a durable logical worker with state, inbox, role, and current assignment, but not a separate OS process.
- A `background job` is a long-running command or workflow tracked outside the active chat turn.

This replaces the thread-and-file polling style of `s_full.py` with a more controlled event-driven backend.

## Data Flow

1. User sends a message from the GUI.
2. Frontend calls the backend to append the user message to a session.
3. `Session Engine` starts an agent turn.
4. Model output streams back to the GUI over WebSocket.
5. Tool calls are executed through `Tool Broker` and written to execution logs.
6. Tool results are reinjected into the turn context.
7. Tasks, teammate events, approvals, background jobs, and summaries are persisted to `SQLite`.
8. The GUI updates from the event stream instead of polling text output.

## Persistence

Use `SQLite` as the primary durable store.

Initial tables should cover:

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

Workspace files remain on disk for transcripts, artifacts, attachments, and caches, but they are not the primary coordination layer.

## GUI Structure

The first release uses a three-column cockpit layout.

- Left column: `Session / Workspace`
  Shows sessions, current workspace, recent activity, and high-level runtime state.
- Center column: `Main Conversation`
  Shows the agent timeline, streaming responses, tool calls, tool results, subagent summaries, background job completions, and message input.
- Right column: `Operations Panel`
  Contains tabs for `Tasks`, `Teammates`, `Approvals`, and `Logs`.

The center column is not a plain chat window. It is a turn timeline that mixes natural-language responses with structured runtime events.

## V1 Scope

### Must Have

- Single-user local desktop app
- `Tauri` shell with a web frontend
- Python backend with session restore
- Lead-agent multi-turn chat
- Streaming output
- Tools: `bash`, `read_file`, `write_file`, `edit_file`
- Task creation, update, view, claim, and dependencies
- Subagent creation and result summaries
- Teammate creation, status display, and messaging
- Approval handling for plan review and sensitive actions
- Background jobs with progress states
- Execution logs and basic audit trail
- Basic compaction support

### Deferred To V2

- Multi-user support
- Remote collaboration
- Plugin marketplace
- Complex role-based access control
- Full worker-process isolation
- Rich memory and retrieval systems
- Advanced auto-claim and routing strategies
- Multi-workspace orchestration

## Technology Choices

- Desktop shell: `Tauri v2`
- Frontend: `React + TypeScript + Vite`
- Backend API/runtime: `FastAPI + asyncio + uvicorn`
- Persistence: `SQLite + SQLAlchemy`
- LLM integration: provider abstraction under `providers/`

The Rust layer should remain thin in `v1`. Agent logic lives in Python, and the frontend talks to the backend over local `HTTP + WebSocket`.

## Repository Structure

```text
Jarvis/
  frontend/
    src/
      app/
      components/
      features/
        chat/
        tasks/
        teammates/
        approvals/
        logs/
      hooks/
      lib/
      types/
  src-tauri/
    src/
    tauri.conf.json
  backend/
    app/
      api/
      core/
      db/
      models/
      providers/
      runtime/
      schemas/
      services/
      storage/
      tools/
    tests/
    main.py
  docs/
    superpowers/
      specs/
```

## Error Handling And Recovery

- Every tool execution records status, timestamps, and truncated output.
- Every approval request has an explicit lifecycle.
- Background jobs survive beyond a single chat turn.
- Sessions can be reopened from persisted state.
- Cancellation is first-class and visible in the UI.

## Testing Strategy

- Unit tests for tool broker, task logic, teammate messaging, and approval flows
- Integration tests for session turns, background jobs, and WebSocket event delivery
- Frontend component tests for the cockpit panels
- End-to-end smoke tests for local startup and a basic agent round-trip

## Rationale For Not Reproducing `s_full.py` Literally

`s_full.py` is the product reference, not the implementation template. Its mechanisms are useful, but the production-oriented version should upgrade three areas:

- Replace file-based coordination with `SQLite`
- Replace thread polling with controlled async scheduling
- Replace console printing with structured UI events and audit records

## Acceptance Criteria For Implementation Planning

The implementation plan should assume this design is accepted if it preserves:

- The approved module boundaries
- The approved runtime model
- The three-column cockpit UI
- The `v1` must-have feature set
- The local-only, single-user constraint
