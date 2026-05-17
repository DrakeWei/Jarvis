# Jarvis Plan Mode Implementation Plan

Date: 2026-05-17
Depends on: `docs/superpowers/specs/2026-05-17-jarvis-plan-mode-design.md`

## Delivery Strategy

Implement Plan Mode as a turn-scoped, next-message-only planning workflow in four phases:

1. API and persistence plumbing
2. Runtime planning behavior and tool restrictions
3. Approval and execution handoff
4. Frontend controls, recovery, and verification

The plan intentionally reuses the current turn, checkpoint, approval, and recovery systems instead of creating a parallel planning subsystem.

## Phase 1: API And Persistence Plumbing

### Objectives

- Persist planning intent at the request boundary
- Make plan turns visible to runtime, recovery, and UI code

### Tasks

- Extend the message submission payload with `execution_mode`
- Add turn-scoped execution mode persistence:
  - `normal`
  - `plan`
- Extend checkpoint context so planning turns can be resumed or recovered correctly
- Extend approval records or approval context handling to support `plan_execution`
- Expose plan-related status through session state and timeline queries where needed

### Exit Criteria

- A submitted request can carry `execution_mode=plan`
- A created turn durably records whether it is a planning turn
- Planning state survives restart and checkpoint recovery

## Phase 2: Runtime Planning Behavior And Tool Restrictions

### Objectives

- Make planning turns read-only
- Ensure the assistant returns a plan instead of performing writes

### Tasks

- Add a planning-mode branch to runtime context assembly and system prompt construction
- Define a hard allowlist for tools available in planning turns
- Block side-effecting tools in planning turns, including:
  - `write_file`
  - `edit_file`
  - `bash`
  - `generate_image`
  - `create_task`
  - `run_subagent`
  - teammate mutation tools
- Adjust runtime execution so blocked tools return clear plan-mode errors instead of silently running
- Shape plan replies into a consistent structured text format:
  - goal
  - findings or assumptions
  - execution steps
  - risks or open questions

### Exit Criteria

- A planning turn can inspect the workspace but cannot perform writes
- The assistant returns a plan instead of claiming work is already done
- Tool filtering is enforced in runtime, not only via prompt instructions

## Phase 3: Approval And Execution Handoff

### Objectives

- Turn a completed plan into an explicit execute-or-revise decision
- Launch normal execution from approved plan context exactly once

### Tasks

- Add a `plan_execution` approval type
- Create a `plan_execution` approval automatically after a successful planning turn
- Store the original user request, plan text, and source turn id in approval context
- Add runtime handling for plan approval resolution:
  - on approve, start a fresh normal execution turn
  - on reject, resolve approval and leave the session idle
- Ensure resume and idempotency behavior matches the current approval system
- Emit timeline events that distinguish plan completion from execution approval

### Exit Criteria

- A successful planning turn produces an actionable `Execute Plan` decision
- Approving the plan starts exactly one execution turn
- Rejecting the plan performs no side effects

## Phase 4: Frontend Controls, Recovery, And Verification

### Objectives

- Let users opt into Plan Mode cleanly from the composer
- Surface plan approvals and recovery state using existing interaction patterns

### Tasks

- Add a next-turn `Plan Mode` toggle to the composer controls
- Reset the toggle to off after submission
- Send `execution_mode` with message requests
- Render `plan_execution` approvals as an inline decision bar above the composer
- Distinguish planning turns from normal turns in the timeline
- Ensure recovered pending plan approvals render correctly after refresh or restart
- Add or update tests for composer toggle behavior, runtime plan restrictions, approval resolution, and recovery

### Exit Criteria

- A user can enable Plan Mode for the next message from the UI
- Plan approvals are visible and actionable without leaving the conversation surface
- Recovery behavior for pending plan confirmations is consistent with existing approval recovery

## Suggested Implementation Order

1. Extend request payloads and turn persistence with `execution_mode`
2. Add runtime plan-mode prompt and hard tool allowlist
3. Add `plan_execution` approval creation and resolution
4. Add composer toggle and inline plan approval UI
5. Add recovery handling and verification coverage

## Files And Areas Expected To Change

- `backend/app/api/routes.py`
- `backend/app/runtime/manager.py`
- `backend/app/services/approval_service.py`
- `backend/app/services/turn_service.py`
- `backend/app/db/session.py`
- `backend/app/models/entities.py`
- `backend/app/schemas/approvals.py`
- `backend/app/schemas/events.py` or related message payload schemas
- `backend/tests/`
- `frontend/src/app/App.tsx`
- `frontend/src/app/styles.css`
- `frontend/src/lib/api.ts`

## Risks To Manage

- Accidentally leaving a side-effecting tool available in Plan Mode
- Mixing `plan_execution` approval UX too closely with `bash` approval UX
- Letting planning turns bypass existing restart and idempotency guarantees
- Making the composer control feel sticky or confusing for simple tasks

## Definition Of Done

Plan Mode is done when a user can enable it for the next message, Jarvis can inspect but not modify the workspace during that turn, the assistant returns a plan, the user can explicitly approve or reject execution, approval resolution is durable and recoverable, and approved plans launch exactly one later execution turn.
