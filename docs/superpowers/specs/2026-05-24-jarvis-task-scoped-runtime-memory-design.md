# Jarvis Task-Scoped Runtime And Memory Redesign

Date: 2026-05-24
Status: Approved in conversation, spec written for review

## Summary

Redesign Jarvis so `task`, not `session`, becomes the primary runtime and memory scope.

The current system stores transcript, memory, and turn state mostly at session scope, then uses heuristics to infer where the current task begins. That model is causing cross-task contamination inside a single session: old goals, progress, rolling summaries, and artifacts leak into new work, which produces irrelevant answers and unstable routing behavior.

The new design makes task routing explicit and durable:

- `session` becomes a container for multiple tasks
- `task` becomes the unit of execution, memory, recovery, and completion
- a structured classifier decides whether a new user message continues the active task, resumes a suspended task, or creates a new task
- all default context assembly, memory retrieval, and recovery operate only within the selected task

This design intentionally accepts a schema break. It does not try to preserve old `sessions/messages/turns/session_memory` compatibility.

## Goal

Jarvis should stop treating a session as a single continuously coherent task.

Instead, it should:

- maintain multiple tasks inside one session
- guarantee that only one task is `active` at a time
- suspend the previous active task when a new task is started
- resume the most recent suspended task for ambiguous continuation requests such as `继续`
- isolate transcript, memory, checkpoints, and completion state by task
- replace keyword-heavy task boundary guesses with a structured classifier-driven routing step

## Non-Goals

This design does not include:

- frontend task UI changes
- backward compatibility with existing session-scoped runtime state
- vector retrieval or embedding-based memory
- multi-active-task concurrency inside one session
- a universal event-sourced architecture for all runtime state
- automatic cross-task context sharing by default

## Problem

Jarvis currently has two incompatible ideas of "current task":

1. short-term transcript slicing uses session-level heuristics to guess a current task cluster
2. long-term memory retrieval still works over session-scoped memory rows

That produces three structural failures:

1. **No durable task scope**  
   Messages, memories, and turns are not consistently bound to a real task identity.

2. **Session-level memory contamination**  
   A single session-level `goal`, `rolling_summary`, and progress stream mixes unrelated work.

3. **Heuristic routing fragility**  
   The system relies on text patterns and task-root guesses instead of explicit state transitions.

The result is exactly the observed bad case: a single session can contain multiple finished and unfinished tasks, but the next answer is still influenced by older goals, summaries, or artifacts.

## Approaches Considered

### Approach A: Keep Current Model And Add Task Tags

Add `task_id` to existing session-scoped records, but keep session-first runtime structure.

This would reduce some contamination, but it preserves the deeper problem: most runtime logic would still start from session state and only later try to filter down to a task. That leaves too many leakage points.

### Approach B: Task Becomes A First-Class Runtime Scope

Make `task` the durable unit for transcript, memory, turn execution, recovery, and completion. Keep `session` as the outer container only.

This is the recommended approach because it fixes the model at the right abstraction level.

### Approach C: Full Event-Sourced Task Graph

Represent all messages, classifications, state transitions, memory writes, and executions as one unified append-only event log and derive task state from projections.

This is architecturally clean but too expensive for the current product stage.

## Product Decision

Choose Approach B.

Jarvis should be redesigned around task-scoped runtime state with classifier-driven routing and explicit task status transitions.

## Core Invariants

The redesign should enforce these invariants:

1. A session may contain many tasks, but only one task may be `active` at a time.
2. Every durable user or assistant message must belong to exactly one task.
3. Every turn, checkpoint, reflection, and task memory row must belong to exactly one task.
4. Default context assembly may only use the current active task.
5. Cross-task recall is never implicit. It must happen through task routing or explicit search.
6. If routing is uncertain, creating a new task is safer than contaminating an old task.

## Runtime Model

### Session And Task Roles

`session` becomes a durable container that groups related tasks by workspace and conversation shell. It is no longer the scope for current goal, current memory, or current execution state.

`task` becomes the unit of:

- message ownership
- execution turns
- checkpoints
- reflections
- memory
- completion
- suspension and resume

### Task Status Model

Each task must use one of these statuses:

- `active`
- `suspended`
- `completed`
- `failed`
- `cancelled`

Exactly one task per session may be `active`.

When a new task is created while another task is active, the previous active task becomes `suspended`.

### Continuation Semantics

When the user sends a continuation-only message such as `继续`:

- if there is an `active` task, continue that task
- otherwise, resume the most recent `suspended` task by `suspended_at desc`

No session-wide heuristic transcript slicing should participate in this decision.

## Storage Model

This design accepts a schema break. The recommended durable model is:

### `sessions`

Keep only session container fields such as:

- `id`
- `title`
- `workspace_path`
- `workspace_fingerprint`
- `repo_root`
- `branch_context_id`
- `created_at`
- `updated_at`

Session rows should not store active goal or rolling summary content.

### `tasks`

Primary task table.

Suggested fields:

- `id`
- `session_id`
- `status`
- `title`
- `summary`
- `origin`
- `created_at`
- `updated_at`
- `activated_at`
- `suspended_at`
- `completed_at`

`origin` should capture how the task was created, such as:

- `user_request`
- `classifier_split`
- `manual_resume`

### `task_messages`

Every durable user or assistant message belongs to one task.

Suggested fields:

- `id`
- `task_id`
- `role`
- `content`
- `created_at`

There should be no steady-state message rows that belong only to a session.

If the implementation needs a brief pre-routing persistence step, it should be treated as transactional transient state rather than a durable long-lived unassigned message model.

### `task_turns`

Every execution turn belongs to one task.

Suggested fields:

- `id`
- `task_id`
- `user_message_id`
- `status`
- `execution_mode`
- `resume_hint`
- `started_at`
- `updated_at`
- `completed_at`

### `task_memory`

This replaces `session_memory`.

Suggested fields:

- `id`
- `task_id`
- `kind`
- `content`
- `source_turn_id`
- `status`
- `salience`
- `path_ref`
- `created_at`
- `updated_at`

All current memory kinds such as `goal`, `progress`, `decision`, `artifact`, `open_question`, and `rolling_summary` become task-scoped.

### `task_state_transitions`

Record every task state change for auditability.

Suggested fields:

- `id`
- `task_id`
- `from_status`
- `to_status`
- `reason`
- `trigger_message_id`
- `trigger_turn_id`
- `created_at`

### `task_classifications`

Persist classifier output instead of treating it as an in-memory hint.

Suggested fields:

- `id`
- `session_id`
- `message_id`
- `active_task_id`
- `decision`
- `target_task_id`
- `confidence`
- `rationale_json`
- `created_at`

## Task Routing Classifier

### Purpose

The classifier is the formal routing gate between message persistence and turn creation.

Its job is not to summarize intent in free text. Its job is to decide which task should own the new user message.

### Position In The Runtime

The classifier runs:

1. after the raw user message is durably persisted
2. before task binding is finalized
3. before task state transitions
4. before turn creation
5. before context assembly

This removes the current failure mode where the system first starts executing at session scope and only later guesses where the task boundary was.

### Input Contract

The classifier should not consume the whole session transcript. It should receive a bounded task-oriented snapshot:

- `latest_user_message`
- `session_snapshot`
  - `session_id`
  - `workspace_path`
  - `branch_context_id`
- `active_task`
  - `task_id`
  - `title`
  - `status`
  - `summary`
  - recent message summary
  - recent memory summary
- `suspended_tasks`
  - recent suspended task summaries only
- `continuation_hint`
  - whether the message is continuation-only
- `runtime_policy`
  - one active task per session
  - continuation with no active task resumes the most recent suspended task

This keeps the classification surface small and reduces contamination from unrelated transcript history.

### Output Contract

The classifier must emit structured output.

Required fields:

- `decision`
- `confidence`
- `reason_codes`
- `evidence`

Optional fields:

- `target_task_id`
- `proposed_title`

`decision` must be one of:

- `continue_active_task`
- `resume_suspended_task`
- `create_new_task`

`target_task_id` is required for `resume_suspended_task` and forbidden for `create_new_task`.

### Routing Safety Policy

The runtime must not trust free-text rationale. It should only execute validated structured output.

Safety rules:

- invalid classifier output falls back to `create_new_task`
- nonexistent or invalid `target_task_id` falls back to `create_new_task`
- low-confidence ambiguous results should prefer `create_new_task`
- continuation-only messages with no active task may deterministically resume the most recent suspended task

The safety bias is intentional:

> creating an extra task is cheaper than contaminating the wrong existing task

## Memory And Context Model

### Task-Scoped Memory

All task-relevant durable memory must move to `task_memory`.

That includes:

- `goal`
- `progress`
- `decision`
- `artifact`
- `open_question`
- `rolling_summary`

No default retrieval path should read memory rows from other tasks in the same session.

### Summary Layers

The redesign should use three summary layers with separate responsibilities:

1. `task.summary`
   - short task overview used for routing and task list summaries
2. `task_memory.rolling_summary`
   - rolling summary used only for that task's future context
3. optional `session.summary`
   - task directory metadata only, not conversation content

`session.summary` must not become a replacement for task memory. It should describe task inventory, not task details.

### Context Assembly

Replace session-first context assembly with task-first context assembly.

The main model should receive:

- stable system prompt
- current task summary
- recent messages from the current task
- current task memory block
- current task artifacts and task-local workspace facts
- current task turn recovery state

The main model should not receive other task transcript or memory by default.

### Cross-Task Search

Cross-task recall should be explicit, not implicit.

Recommended internal tools:

- `search_current_task_memory`
- `search_current_task_conversation`
- `search_session_tasks`

The main agent should default to the first two. The third exists for routing and explicit recall only.

## Recovery And Completion Scope

### Task-Scoped Recovery

Recovery should restore task state, not session state.

Recommended recovery order:

1. resume unfinished work for the current active task
2. if the user asks to continue and there is no active task, resume the most recent suspended task
3. never auto-inject another task's checkpoint or transcript into the current task

### Task-Scoped Completion

Completion and reflection should operate on the current task only.

When a turn reaches a terminal accepted outcome, the current task becomes:

- `completed`
- or `failed`
- or `cancelled`

If the user pivots to unrelated work before the current task finishes, the task becomes `suspended`, not terminal.

## End-To-End Runtime Flow

The new backend flow should be:

1. **Persist user message**
   - store the raw user message durably inside the routing transaction
   - do not leave long-lived unassigned message rows after the transaction commits

2. **Classify task routing**
   - load the current active task and recent suspended task summaries
   - run the structured classifier

3. **Apply task state transition**
   - `continue_active_task`: no task switch
   - `resume_suspended_task`: previous active becomes `suspended`, target becomes `active`
   - `create_new_task`: previous active becomes `suspended`, new task becomes `active`

4. **Bind the message to the selected task**
   - finalize `task_id` ownership for the current message

5. **Create task turn**
   - create a new turn bound to the selected task

6. **Write initial task memory**
   - write user intent into current task memory
   - update the current task rolling summary

7. **Assemble task-scoped context**
   - build model input from the selected task only

8. **Run the agent loop**
   - all checkpoints, tool executions, and assistant memory writes stay within the selected task

9. **Apply task-scoped completion**
   - if done, set the task to a terminal state
   - otherwise keep it `active` or let later routing suspend it

Two ordering rules are mandatory:

- message assignment happens before context assembly
- task transition happens before turn creation

These rules prevent split-brain failures where the runtime executes under one task scope but writes state into another.

## Error Handling

The runtime should harden around classifier and transition errors:

- invalid classifier schema -> `create_new_task`
- invalid target task reference -> `create_new_task`
- ambiguous low-confidence routing -> `create_new_task`
- multiple active tasks detected -> fail the transaction and repair uniqueness
- continuation with no active or suspended tasks -> no implicit history guess

The design must prefer predictable state over clever fallback behavior.

The database should also enforce the one-active-task invariant with a uniqueness constraint or equivalent transactional guard.

## Testing Strategy

Testing should focus on runtime semantics, not only helper functions.

Recommended coverage:

- creating a new task suspends the previous active task
- continuation resumes the most recent suspended task
- task memory retrieval never leaks other task memory
- context assembly only uses the active task
- task recovery never restores non-active task state into the current context
- invalid classifier output falls back to new task creation
- a session never ends up with two active tasks
- completed tasks are not default continuation targets
- suspended task ordering prefers the most recent one
- artifacts from another task do not pollute current task memory

## Acceptance Criteria

The redesign is successful when all of the following are true:

- multiple unrelated tasks can exist in one session without contaminating each other's default context
- ambiguous continuation requests resume the most recent suspended task
- the database and runtime both enforce one active task per session
- completion, reflection, checkpointing, and recovery all operate by `task_id`
- the runtime no longer depends on session-level task-root heuristics as the primary task boundary mechanism
- session-scoped rolling summaries no longer drive normal model context

## Consequences

This design deliberately makes the system more explicit and less magical.

That raises implementation cost in exchange for much stronger behavioral stability:

- task boundaries become durable state instead of prompt inference
- memory contamination is blocked structurally instead of heuristically
- continuation behavior becomes deterministic
- future work such as task lists, explicit task switching, or task-level analytics becomes much easier because the backend model is already correct
