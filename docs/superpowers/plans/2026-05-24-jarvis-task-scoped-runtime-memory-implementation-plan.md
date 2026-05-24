# Jarvis Task-Scoped Runtime And Memory Implementation Plan

Date: 2026-05-24
Depends on: `docs/superpowers/specs/2026-05-24-jarvis-task-scoped-runtime-memory-design.md`

## Delivery Strategy

Implement the task-scoped runtime redesign in five phases:

1. schema cutover and task-scoped service foundations
2. classifier-driven task routing and state transitions
3. task-scoped runtime, memory, and context assembly
4. task-scoped recovery, completion, and search surfaces
5. regression coverage, eval alignment, and dead-code removal

The plan intentionally treats this as a backend cutover rather than a compatibility layer. The first milestone is not "support both models." It is "make task the only valid runtime scope."

## Phase 1: Schema Cutover And Task-Scoped Service Foundations

### Objectives

- replace session-scoped transcript and memory ownership with task-scoped ownership
- establish durable task status and transition primitives
- remove the old schema as the default runtime source of truth

### Tasks

- Add new core tables:
  - `tasks`
  - `task_messages`
  - `task_turns`
  - `task_memory`
  - `task_state_transitions`
  - `task_classifications`
- Reduce `sessions` to container responsibilities only:
  - workspace identity
  - repo identity
  - branch context identity
  - title and timestamps
- Add database enforcement for the one-active-task invariant:
  - unique partial index or equivalent transactional guard on `tasks(session_id)` where `status='active'`
- Introduce task-scoped model and schema types for:
  - tasks
  - task messages
  - task turns
  - task memory
  - task classifications
  - task transitions
- Add new service modules or replacements for:
  - `task_service`
  - `task_message_service`
  - `task_turn_service`
  - `task_memory_service`
  - `task_classification_service`
  - `task_transition_service`
- Remove old session-scoped memory from the main path:
  - stop reading `session_memory` during normal runtime
  - stop writing new task information into `session_memory`
- Decide and implement cutover behavior for legacy tables:
  - either drop them in dev/test initialization
  - or keep them inert but unreachable from the new runtime

### Exit Criteria

- all new durable runtime writes have a `task_id`
- the database rejects two active tasks in one session
- session-scoped memory is no longer part of the normal runtime path

## Phase 2: Classifier-Driven Task Routing And State Transitions

### Objectives

- make task routing an explicit backend step before turn creation
- replace heuristic task-root guessing with structured routing decisions
- make every task switch durable and auditable

### Tasks

- Add a dedicated task routing service that:
  - loads the active task snapshot
  - loads recent suspended task summaries
  - prepares classifier input
- Add a structured classifier contract with validated output:
  - `continue_active_task`
  - `resume_suspended_task`
  - `create_new_task`
- Add a routing transaction that performs, in order:
  - raw user message persistence
  - classifier call
  - task status transition
  - message-to-task binding
  - task turn creation
- Persist every classifier result to `task_classifications`
- Persist every task status change to `task_state_transitions`
- Add deterministic continuation handling:
  - if there is an active task, `继续` continues it
  - if there is no active task, `继续` resumes the most recent suspended task
- Define low-confidence and invalid-output fallback behavior:
  - invalid classifier output -> create new task
  - invalid target task -> create new task
  - ambiguous low-confidence routing -> create new task
- Remove session-level task-root heuristics from the main flow:
  - stop relying on transcript slicing to infer task boundaries before routing

### Exit Criteria

- every new message is explicitly routed to one task before execution starts
- task switches are durable and queryable
- continuation behavior is deterministic and no longer depends on session transcript heuristics

## Phase 3: Task-Scoped Runtime, Memory, And Context Assembly

### Objectives

- make the current active task the only default model-facing context scope
- move runtime memory writes and reads to task scope
- remove session-first context assembly from the execution path

### Tasks

- Replace session-scoped memory services with task-scoped equivalents for:
  - `goal`
  - `progress`
  - `decision`
  - `artifact`
  - `open_question`
  - `rolling_summary`
- Add task summary maintenance:
  - short task summary for routing and suspended-task previews
  - rolling task summary for task-local context reuse
- Rewrite context assembly to accept `task_id` as the primary input
- Ensure the main model context contains only:
  - active task summary
  - active task recent messages
  - active task memory
  - active task artifacts
  - active task workspace facts
  - active task recovery hints
- Stop injecting default context from:
  - session-wide message history
  - session-wide memory retrieval
  - legacy rolling session summaries
- Update all assistant-side memory capture paths so they write into the current task only
- Update asset and artifact association so generated files and task-relevant paths are attributed to the current task
- Add explicit cross-task search surfaces:
  - `search_current_task_memory`
  - `search_current_task_conversation`
  - `search_session_tasks`
- Keep cross-task recall opt-in rather than implicit

### Exit Criteria

- normal model calls no longer receive other-task transcript or memory by default
- all runtime memory writes are task-scoped
- cross-task lookup is explicit and separated from normal context assembly

## Phase 4: Task-Scoped Recovery, Completion, And Search Surfaces

### Objectives

- make task, not session, the unit of recovery and completion
- ensure interrupted work resumes coherently inside one task scope
- keep search, reflection, and completion aligned with the selected task

### Tasks

- Move turn recovery to `task_turns`:
  - running turn recovery
  - interrupted turn recovery
  - waiting-approval recovery
- Ensure only the current active task can be auto-recovered into the main runtime context
- Update checkpoint persistence to bind checkpoints to task turns
- Update reflection and completion logic to operate on the current task only
- Ensure terminal completion sets task state to:
  - `completed`
  - `failed`
  - `cancelled`
- Ensure task interruption by unrelated new work sets the task to `suspended`, not terminal
- Update conversation and memory search services so default queries are task-scoped
- Add session-level task listing/search for routing support and future product surfaces
- Review approval and background job code paths so they always carry task identity where needed

### Exit Criteria

- recovery restores task-local execution state rather than session-wide execution state
- completion and reflection no longer read or mutate unrelated task state
- default search behavior respects task boundaries

## Phase 5: Regression Coverage, Eval Alignment, And Dead-Code Removal

### Objectives

- lock in the new routing and memory semantics with regression coverage
- align eval and observability with task-scoped state
- remove the old session-first code paths so they cannot silently reappear

### Tasks

- Add regression tests for:
  - new task creation suspending the prior active task
  - continuation resuming the most recent suspended task
  - task memory isolation across unrelated tasks in one session
  - context assembly using only the active task
  - recovery restoring only active-task state
  - invalid classifier output falling back to new task creation
  - active-task uniqueness enforcement
  - completed-task exclusion from default continuation targets
  - cross-task artifact leakage prevention
- Add service-level tests for:
  - task transitions
  - task classification persistence
  - task search ordering
  - transactional routing correctness
- Update eval fixtures and runtime evidence capture so task identity is visible in traces where relevant
- Update observability and debug metadata to expose:
  - selected task id
  - routing decision
  - transition reason
  - recovered task id
- Remove or retire old code paths that depend on session-scoped task inference:
  - session-level rolling summary injection for normal runtime
  - session-first current-task cluster detection
  - legacy session memory retrieval from the main execution path

### Exit Criteria

- known bad cases replay without cross-task contamination
- debug output can explain why a task was continued, resumed, or created
- obsolete session-first routing paths are removed or rendered unreachable

## Suggested Implementation Order

1. Add new task-scoped tables and uniqueness enforcement
2. Add task-scoped models, schemas, and service foundations
3. Implement classifier-driven routing transaction and durable transitions
4. Rewrite runtime memory writes and reads to task scope
5. Rewrite context assembly to be task-first
6. Move recovery, checkpoints, and completion to task scope
7. Add task-scoped search and observability surfaces
8. Remove obsolete session-first routing and memory paths
9. Expand regression and eval coverage around known bad cases

## Files And Areas Expected To Change

- `backend/app/models/entities.py`
- `backend/app/models/__init__.py`
- `backend/app/db/session.py`
- `backend/app/schemas/`
- `backend/app/services/`
  - `task_service.py`
  - `task_message_service.py`
  - `task_turn_service.py`
  - `task_memory_service.py`
  - `task_classification_service.py`
  - `task_transition_service.py`
  - `context_assembler.py`
  - `conversation_search_service.py`
  - `memory_search_service.py`
  - `checkpoint_service.py`
  - `session_service.py`
- `backend/app/runtime/manager.py`
- `backend/app/api/routes.py`
- `backend/evals/`
- `backend/tests/`

## Risks To Manage

- implementing classifier routing without strong transactional boundaries and ending up with mismatched message and task ownership
- keeping too much legacy session-first code alive and reintroducing memory leakage through side paths
- under-specifying task summaries so suspended-task routing becomes unstable
- relying on model routing output without hard validation and corrupting task state
- migrating recovery and approval paths incompletely so old task state leaks back into the active runtime

## Definition Of Done

The task-scoped runtime redesign is done when Jarvis can host multiple unrelated tasks inside one session, keep exactly one active task at a time, suspend previous work when a new task begins, deterministically resume the most recent suspended task for continuation-only requests, assemble normal model context strictly from the active task, recover and complete work by task scope, and replay known multi-task bad cases without cross-task memory contamination or session-first routing fallbacks.
