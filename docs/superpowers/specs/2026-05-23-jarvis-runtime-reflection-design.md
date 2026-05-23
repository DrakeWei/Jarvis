# Jarvis Runtime Reflection Design

## Goal

Introduce a structured Reflection subsystem into the Jarvis lead-agent runtime so the agent does not terminate immediately when the model stops producing tool calls.

This phase has two concrete goals:

1. add a single structured reflection step at the no-tool-call boundary in the ReAct loop
2. make reflection durable so restart, recovery, and observability do not lose critic state

The intent is not to add per-turn or per-step critic overhead. This design adds one explicit reflection checkpoint at the completion boundary.

## Problem

Jarvis already has heuristic correction logic in the runtime, but it is still scattered and in-memory only.

Today, the main symptoms are:

- the lead loop can finalize too early when the model stops after inspection
- correction logic is encoded as follow-up prompt generation instead of a first-class runtime decision
- there is no durable record of why the runtime chose to continue, finalize, or stop as blocked
- restart and recovery can restore tool and turn state, but not structured completion diagnosis

This creates three product problems:

1. early termination remains possible on coding and time-sensitive research tasks
2. runtime behavior is harder to reason about because completion gating is implicit
3. observability and recovery lose a critical part of agent decision state

## Scope

This design covers one focused runtime upgrade:

- a structured `ReflectionDecision` at the lead-agent completion boundary
- durable persistence for reflection records
- checkpoint coverage before and after reflection
- test migration from heuristic follow-up assertions to reflection assertions

This phase includes:

- runtime reflection decision logic
- reflection persistence model and service
- checkpoint phase expansion
- runtime recovery compatibility
- unit tests for reflection outcomes

## Non-Goals

This phase does not include:

- per-iteration critic calls
- a separate reflection model provider or external critic service
- a broad taxonomy of failure classes beyond the four agreed reason codes
- UI work for reflection inspection
- eval runner integration beyond keeping the runtime compatible with existing eval flows

## Principles

### Reflection Runs Only At The Completion Boundary

Reflection should happen when the model has stopped producing tool calls and appears ready to finish. It should not become a second inner loop that runs on every step.

### Reflection Must Be Structured

The runtime decision should be explicit and typed, not hidden inside ad hoc prompt text generation.

### Durable State Matters As Much As Decision Quality

If turns, checkpoints, and tool executions are durable, reflection must also be durable. Restarting the runtime should not erase why the system was about to continue or stop.

### Reuse Existing Signals Before Inventing New Ones

The current runtime already has useful heuristics for code-change detection, verification detection, web-search requirements, evidence quality, and blocker language. Reflection should consolidate these signals instead of replacing them with a larger speculative system.

### Blocked Must Be User-Readable

A blocked verdict is allowed to terminate the turn, but the user-facing output must clearly explain the blocker. The runtime should preserve the model's original final text when it already does this well.

## Success Criteria

This phase is successful only if all of the following are true:

- lead-agent turns no longer finalize directly from the `if not tool_calls` branch
- the runtime emits a structured reflection decision before finalizing or re-entering the loop
- reflection decisions survive process restart through durable records and checkpoints
- the runtime can explain why it chose `done`, `continue`, or `blocked`
- existing heuristic completion corrections are replaced by reflection-driven control flow

## Runtime Design

### Trigger Point

Reflection runs only in `RuntimeManager._continue_agent_loop()` when the model response contains no tool calls.

This replaces the current direct completion path:

1. collect `final_text` from the returned text blocks
2. run reflection against the current loop state
3. persist the reflection decision
4. branch on `verdict`

The iteration limit and all normal tool-execution behavior remain unchanged.

### ReflectionDecision Shape

Reflection returns a structured object with four fields:

- `verdict`
- `reason_codes`
- `next_action_prompt`
- `summary`

The allowed values are:

- `verdict: done | continue | blocked`
- `reason_codes: missing_edit | missing_verification | weak_external_evidence | wrong_tool_choice`

This object should live as a small runtime dataclass such as `ReflectionDecision`.

### Verdict Semantics

#### done

`done` means the current turn is ready to finalize. The runtime returns the model's final text as usual.

This verdict is valid only when reflection finds no missing action that should keep the loop alive.

#### continue

`continue` means the agent stopped too early and should re-enter the existing ReAct loop.

The runtime should:

1. append an internal `user` message containing `next_action_prompt`
2. preserve the existing loop context
3. continue the next normal iteration

This is intentionally lightweight. Reflection does not create a new planning subsystem. It only injects one internal continuation instruction.

#### blocked

`blocked` means the loop may terminate, but only because the runtime has a concrete reason it cannot safely continue.

The user-facing final text should follow this policy:

1. preserve the model's original `final_text` when it already explains the blocker clearly
2. if the original final text does not explain the blocker clearly enough, append or replace with the reflection `summary`

This preserves model-authored explanations when they are already acceptable while ensuring the user still gets an explicit blocker.

## Reason Code Design

Reason codes remain intentionally narrow in this phase. Reflection should support multiple reason codes on the same decision rather than forcing a single primary cause.

### missing_edit

Use `missing_edit` when:

- the user request is a code-change task
- the turn has not produced a successful write action
- the agent is trying to finish without making the required change

This captures the current early-stop class where the agent inspects repository state but never edits.

### missing_verification

Use `missing_verification` when:

- the turn already produced a successful write action
- no verification attempt has occurred
- the final text does not clearly explain why verification could not run

This covers the common class where the agent edits files and then stops with a summary of edits instead of running `run_test` or surfacing a concrete blocker.

### weak_external_evidence

Use `weak_external_evidence` when:

- the task requires time-sensitive external information
- `web_search` was used
- the returned evidence quality is weak
- the final text does not clearly communicate uncertainty or limited evidence

This code does not mean the answer must always continue. It means the runtime should not allow an overconfident finalization on weak evidence.

### wrong_tool_choice

Use `wrong_tool_choice` when the agent is approaching the task through the wrong class of tools.

The intended first cases are:

- the task requires time-sensitive external information but no successful `web_search` occurred
- the task requires a code change but the loop has only performed read-only inspection
- the task requires verification but the agent is still trying to terminate without attempting verification or explicitly surfacing a blocker

This code is broader than the other three. It is the runtime's structured explanation for "the next action category is wrong even if the task understanding is roughly correct."

## Decision Rules

Reflection should be implemented as deterministic runtime logic in this phase, not as a second model call.

The runtime should reuse the current helper signals where possible:

- `_task_requires_code_change()`
- `_task_requires_web_search()`
- `_tool_result_history()`
- `_has_successful_write_tool()`
- `_has_verification_attempt()`
- `_latest_web_search_evidence_quality()`
- `_response_explains_blocker()`
- `_response_communicates_uncertainty()`

The recommended rule ordering is:

1. handle empty-response retries first
2. compute the reflection decision after `final_text` exists
3. allow `done` only when no required follow-up action remains
4. prefer `blocked` only when the final text already states a concrete blocker or the runtime can state one concretely
5. otherwise return `continue` with a targeted `next_action_prompt`

This keeps reflection compatible with the current runtime behavior while making the decision explicit.

## Durable State Design

### Persistence Model

Add a new `TurnReflectionRecord` entity alongside turns, checkpoints, and tool executions.

Recommended fields:

- `id`
- `turn_id`
- `checkpoint_id`
- `reflection_seq`
- `verdict`
- `reason_codes_json`
- `next_action_prompt`
- `summary`
- `created_at`

The record should be append-only per turn. A single turn may create multiple reflection records if it reaches the no-tool-call boundary more than once.

### Why A Separate Table

Reflection should not be encoded only inside checkpoint JSON because:

- checkpoint context is optimized for resumability, not queryability
- reflection is a first-class runtime event that deserves direct observability
- durable diagnosis should be queryable without decoding every checkpoint blob

The checkpoint and the reflection record serve different purposes:

- checkpoint: resumable loop context
- reflection record: structured completion diagnosis

### Checkpoint Phases

Extend checkpoint phases with:

- `before_reflection`
- `after_reflection`

Recommended behavior:

1. write `before_reflection` with the current loop context before running reflection
2. create the reflection record
3. write `after_reflection` with the same resumable context plus a compact reflection summary

The `after_reflection` checkpoint should carry only the minimum reflection metadata needed for recovery, such as:

- `reflection_id`
- `verdict`
- `reason_codes`
- `next_action_prompt`

Large reflection payloads should remain in the reflection table, not the checkpoint blob.

### Recovery Semantics

This design should preserve reflection state across restart in the same way turns and checkpoints already preserve tool and approval state.

After restart, the runtime should be able to recover:

- the latest checkpoint context
- the latest reflection record for the turn
- whether the previous loop state was about to continue, finalize, or stop as blocked

This does not require a new recovery subsystem. It requires reflection to participate in the same durable state model that the runtime already uses.

## Implementation Plan

### Runtime Manager

In `backend/app/runtime/manager.py`:

- add a `ReflectionDecision` dataclass
- add `_run_reflection(...)`
- add one or more small helpers to build the structured decision
- add `_finalize_blocked_text(...)` to enforce the blocked-output policy
- replace the `_completion_gate_followup(...)` branch with reflection-driven control flow

The `if not tool_calls:` flow should become:

1. build `final_text`
2. write `before_reflection` checkpoint
3. run `_run_reflection(...)`
4. persist the reflection record
5. write `after_reflection` checkpoint
6. branch on `done`, `continue`, or `blocked`

### Reflection Service

Add `backend/app/services/reflection_service.py` with persistence-only responsibilities:

- `create_reflection(...)`
- `latest_turn_reflection(...)`
- `list_turn_reflections(...)`

Decision policy should remain in the runtime manager. The service should not become a second policy layer.

### Model And Database

In `backend/app/models/entities.py`:

- add `TurnReflectionRecord`

In `backend/app/models/__init__.py`:

- export `TurnReflectionRecord`

In `backend/app/db/session.py`:

- ensure the new table exists via `Base.metadata.create_all`
- add a small migration helper for reflection-specific indexes or forward compatibility if needed
- extend `_ensure_query_indexes()` with reflection indexes such as turn and created-at access paths

This project already uses runtime migration helpers rather than Alembic, so reflection should follow the existing database evolution style.

## Testing

Current tests that assert `_completion_gate_followup(...)` behavior should move to reflection assertions.

Minimum cases:

- code-change task with no successful write => `continue` with `missing_edit`
- successful write with no verification => `continue` with `missing_verification`
- time-sensitive external request with no successful `web_search` => `continue` with `wrong_tool_choice`
- weak web evidence without uncertainty language => `continue` with `weak_external_evidence`
- clear blocker response => `blocked`
- valid completed response => `done`

Tests should also verify that:

- `before_reflection` and `after_reflection` checkpoints are written
- a reflection record is persisted when reflection runs
- blocked finalization preserves the original final text when it already explains the blocker

## Risks And Mitigations

### Risk: Reflection And Existing Heuristics Diverge

Mitigation:

- remove `_completion_gate_followup(...)` as a competing control path
- keep one reflection decision path as the only completion gate

### Risk: Reflection Becomes A Hidden Per-Step Critic Later

Mitigation:

- keep the trigger point narrow and explicit in the runtime
- document that reflection only runs at the no-tool-call boundary in this phase

### Risk: Checkpoint Payload Growth

Mitigation:

- store full reflection detail in `TurnReflectionRecord`
- keep checkpoint reflection metadata compact

### Risk: Overusing wrong_tool_choice

Mitigation:

- keep the first implementation narrow and tied to known runtime mistakes
- add more categories only if stable failure classes emerge later

## Open Questions

There are no blocking design questions remaining for this phase.

Future phases may decide whether reflection should later feed evaluation tags, UI inspection, or adaptive prompting, but this design does not require any of those expansions.

## Recommended Rollout

1. land the persistence model and service
2. switch the runtime no-tool-call branch to reflection
3. migrate existing completion-gate tests
4. run focused runtime regression tests for coding-task completion behavior
