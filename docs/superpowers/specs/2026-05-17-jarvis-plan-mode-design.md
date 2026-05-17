# Jarvis Plan Mode Design

## Goal

Add an optional Plan Mode to Jarvis so a user can force the next request into a read-only planning phase, review the proposed approach, and then decide whether to execute it.

## Problem

Jarvis currently defaults to execution. If the user asks for code changes, the runtime is expected to inspect the workspace and then modify files directly when safe. The only strong built-in pause point today is `bash` approval. That is enough for dangerous shell commands, but not for large refactors, ambiguous feature requests, or broad multi-file changes where the main risk is direction rather than command safety.

Jarvis already has the core building blocks for gated flow:

- turn lifecycle state
- inline approval UI
- interrupted and waiting-approval recovery
- timeline events

What is missing is a first-class pre-execution mode that narrows tools to read-only exploration and requires an explicit user decision before any write-capable execution begins.

## Scope

This spec covers a first implementation of Plan Mode with these boundaries:

- the user can enable Plan Mode from the composer for the next message only
- the resulting turn runs in a read-only planning mode
- the assistant returns a plan instead of executing writes
- the user can approve execution of the plan or ask for revision
- execution happens in a later turn after approval

## Non-Goals

- reproducing Claude Code's full dual-approval Plan Mode flow exactly
- making Plan Mode the default behavior for every request
- adding automatic plan quality scoring or auto-approval
- adding long-lived session-global plan mode that persists across many turns
- introducing a new execution engine separate from the current turn loop

## Approaches Considered

### Approach A: Global Always-On Planning

Every request becomes "plan first, execute second."

This is the safest option for directional errors, but it would heavily slow down ordinary tasks and make Jarvis worse at the quick direct-edit workflow it already supports well.

### Approach B: Composer-Level Next-Turn Plan Toggle

The user explicitly enables Plan Mode for the next message from the composer. That turn becomes read-only, returns a plan, and then waits for execution confirmation.

This keeps the safety benefit for complex work without harming simple requests. This is the recommended approach.

### Approach C: Automatic Heuristic Plan Entry

The runtime decides that a request "looks complex" and enters Plan Mode automatically.

This can be a useful future enhancement, but it is a poor first release because false positives would make behavior feel inconsistent and harder to trust.

## Product Decision

Choose Approach B.

Plan Mode should be a compact composer-attached control for the next submitted message only. It is not a global session switch and it does not silently persist after execution.

## User Experience

### Composer Control

Add a small `Plan Mode` toggle or segmented control below or beside the composer controls.

Behavior:

- off by default
- affects the next submitted user message only
- resets to off after submission
- remains visually attached to the composer rather than becoming a global toolbar

### Planning Turn

When a user submits a message with Plan Mode enabled:

- the backend creates a normal turn, but marks it as a planning turn
- the assistant may inspect files and gather evidence
- the assistant may not write files, run shell commands, or perform other side effects
- the assistant returns a structured plan instead of direct execution

### Post-Plan Decision Surface

After the plan is returned, the UI should show an inline decision bar above the composer using the same visual family as existing approvals.

The first version should offer:

- `Execute Plan`
- `Revise Plan`

`Execute Plan` starts a new execution turn using the approved plan as explicit context.

`Revise Plan` leaves the session idle and lets the user send another request, optionally with Plan Mode enabled again.

## Runtime Model

### Turn Execution Mode

Add a turn-scoped execution mode:

- `normal`
- `plan`

This mode belongs to the turn and checkpoint context, not just frontend local state.

### Tool Policy In Plan Mode

Plan Mode should be read-only.

Allowed tools in phase one:

- `list_files`
- `read_file`
- read-only search or navigation tools
- read-only memory and conversation search
- attachment inspection tools

Blocked tools in phase one:

- `write_file`
- `edit_file`
- `bash`
- `generate_image`
- `create_task`
- `run_subagent`
- `create_teammate`
- `message_teammate`
- any future tool with side effects

The implementation should prefer an explicit allowlist over trying to infer read-only safety from tool names at runtime.

### Assistant Behavior In Plan Mode

The system prompt for a planning turn should explicitly say:

- this turn is in Plan Mode
- only read-only inspection is allowed
- produce a concrete execution plan
- do not modify files or propose that work is already done

The desired output shape is a concise structured plan containing:

- goal
- key findings or assumptions
- execution steps
- risks or open questions

The first version does not need a rigid JSON schema. Well-structured text is sufficient.

## Approval And Confirmation Flow

Plan Mode should not reuse the existing `bash` approval record type directly. It needs a distinct approval or decision type because the semantics are different.

Recommended new approval type:

- `plan_execution`

Suggested flow:

1. User sends a message with Plan Mode enabled.
2. Runtime executes a read-only planning turn.
3. Assistant reply is published normally.
4. Runtime creates a `plan_execution` approval bound to the turn.
5. Frontend renders `Execute Plan` and `Revise Plan`.
6. If approved, runtime starts a fresh execution turn using the approved plan plus the original user request as input context.
7. If rejected, approval is resolved and no execution turn starts.

This preserves the existing "turns are durable, approvals are durable, recovery is durable" architecture instead of inventing a parallel lightweight confirmation path.

## State And Persistence

Persistence additions:

### Extend Turns Or Checkpoints

Persist whether a turn ran in `normal` or `plan` mode.

This must survive restart so recovery and timeline rendering remain accurate.

### Extend Approvals

Support approval records for `plan_execution`, not just `bash`.

The approval payload should include:

- original user request
- approved plan text
- source turn id

### Session State

Expose whether the latest session state is waiting for plan execution confirmation, similar to how waiting bash approval is surfaced today.

## UI And API Changes

### Frontend

The frontend needs:

- a composer-level `Plan Mode` control
- request payload support for the next message execution mode
- inline rendering for `plan_execution` approvals
- timeline copy that distinguishes planning turns from execution turns

The decision bar should stay above the composer, consistent with the current inline approval placement.

### API

Extend the message submission payload with `execution_mode`.

This is the chosen shape because it preserves the user intent at the API boundary and survives any later job or turn creation indirection.

## Recovery Behavior

Plan Mode should integrate with existing recovery rules:

- if a planning turn is interrupted, it becomes `interrupted` like any other turn
- if a plan approval is pending during restart, the approval remains visible after recovery
- approving a recovered `plan_execution` approval should still launch the execution turn exactly once

This is another reason to build on existing turn and approval persistence rather than a frontend-only ephemeral plan state.

## Risks

### Risk: Overloading Approval Semantics

If plan confirmation is mixed indistinguishably with command approval, the UI and user mental model will become muddy.

Mitigation:

- use a distinct approval type
- render distinct copy and actions for plan execution

### Risk: Plan Mode That Still Writes Indirectly

If a side-effecting tool is accidentally left available, Plan Mode loses its core guarantee.

Mitigation:

- define a hard allowlist for plan mode
- add tests that prove blocked tools cannot execute

### Risk: Friction On Simple Tasks

If Plan Mode is too prominent or sticky, users may feel slowed down.

Mitigation:

- keep it off by default
- make it next-turn only
- auto-reset after submit

## Testing Requirements

- API tests for submitting a planning turn
- runtime tests proving plan turns cannot execute side-effecting tools
- approval tests for `plan_execution` create, resolve, and idempotent retry
- recovery tests proving pending plan approvals survive restart
- frontend tests for composer toggle behavior and approval rendering

## Rollout Plan

### Phase 1

Implement manual composer toggle, read-only tool filtering, plan reply generation, and execute-or-revise confirmation.

### Phase 2

Improve plan formatting and timeline visibility.

### Phase 3

Consider heuristic or model-suggested plan entry for complex tasks.

## Acceptance Criteria

- a user can enable Plan Mode for the next message from the composer
- the resulting turn can inspect but cannot modify the workspace
- the assistant returns a plan instead of executing writes
- the user can explicitly approve execution of the plan
- approving the plan starts a later execution turn exactly once
- rejecting the plan performs no side effects
- pending plan confirmations survive restart and recovery
