# Jarvis Goal-Driven Execution Implementation Plan

Date: 2026-05-26
Depends on: `docs/superpowers/specs/2026-05-26-jarvis-goal-driven-execution-design.md`

## Delivery Strategy

Implement goal-driven execution in five phases:

1. completion-level routing and `soft` activation
2. completion packet and blocker extraction
3. reviewer verdict expansion and phase-based runtime routing
4. bounded repair-then-verify flow
5. regression coverage, eval alignment, and observability polish

The plan intentionally preserves the current agent loop shape and upgrades the completion boundary instead of redesigning the whole runtime. The first objective is to make high-risk turns converge on the user goal without forcing every turn through hard verification.

## Phase 1: Completion-Level Routing And `soft` Activation

### Objectives

- make `none | soft | hard` real runtime states instead of a mostly two-state system
- reserve hard completion for world-failure-bearing claims
- route read-only analytical turns through a lighter gate

### Tasks

- Extend `task_profile_service.py` so `TaskProfile` can emit:
  - `verify_level = none | soft | hard`
  - `completion_mode = direct | evidence_check | goal_driven`
- Add explicit detection for read-only analytical tasks such as:
  - repository summaries
  - bug-location hypotheses
  - architecture explanations grounded in inspected files or logs
- Keep purely conversational and user-provided-content turns in `none`
- Keep code changes, dependency installs, runnable artifact claims, and fresh external facts in `hard`
- Update runtime completion entrypoints to branch by `completion_mode` instead of assuming "not none means hard reviewer"

### Exit Criteria

- ordinary chat bypasses completion review
- read-only analytical turns enter `soft`
- code and dependency turns still enter `hard`

## Phase 2: Completion Packet And Blocker Extraction

### Objectives

- upgrade the existing verification packet into a completion packet without a disruptive rename
- represent blockers and repairability as structured runtime facts
- preserve original-goal anchoring

### Tasks

- Extend `verification_packet_service.py` with:
  - `candidate_result_summary`
  - `blockers`
  - `repairable_blockers`
  - `last_failed_action`
  - `last_failed_verification_command`
  - `remaining_repair_attempts`
  - `remaining_verify_attempts`
- Keep existing evidence and artifact summaries that are still useful
- Add structured blocker extraction from failed tool outputs:
  - Python `ModuleNotFoundError`
  - wrong interpreter or wrong environment signals
  - missing entrypoint dependency
  - approval-required shell mutation
- Preserve `original_goal` extraction rules so runtime follow-up prompts do not overwrite the user task
- Ensure `soft` packets include read-only evidence and uncertainty state without repair metadata requirements

### Exit Criteria

- the runtime can distinguish plain verification gaps from repairable blockers
- a failed verification command can be replayed because the packet records it explicitly
- original-goal extraction remains stable

## Phase 3: Reviewer Verdict Expansion And Phase-Based Runtime Routing

### Objectives

- widen reviewer output so it can route to repair, verification, or read-only evidence gathering
- simplify runtime routing so it follows explicit next phases instead of single-purpose retry prompts
- keep `soft` and `hard` reviewer capabilities separate

### Tasks

- Expand reviewer verdicts to:
  - `done`
  - `done_with_uncertainty`
  - `continue_with_read_only_evidence`
  - `continue_with_repair`
  - `continue_with_verification`
  - `blocked`
- Add `next_phase` to runtime-facing reflection decisions:
  - `finalize`
  - `gather_evidence`
  - `repair`
  - `verify`
  - `blocked`
- Split reviewer contracts by completion mode:
  - `soft` reviewer only allows read-only evidence and uncertainty correction
  - `hard` reviewer allows repair and verification
- Update `runtime/manager.py` so reflection routing is phase-based instead of hard-coded around `continue_with_verification`
- Keep task misalignment checks and original-goal anchoring in front of finalization

### Exit Criteria

- the runtime can distinguish evidence gathering from repair
- `soft` turns cannot accidentally enter environment-repair flow
- `hard` turns can move into repair without pretending they are "just verifying"

## Phase 4: Bounded Repair-Then-Verify Flow

### Objectives

- turn repairable blockers into one bounded self-healing path
- keep repair behavior narrow, explicit, and policy-aware
- force re-verification after repair

### Tasks

- Add one repair budget and one verification budget to hard-completion turns
- Define a repair policy for the first phase:
  - allow only narrow, goal-relevant repair suggestions
  - keep repair limited to one attempt
  - require re-verification after repair before finalization
- Prefer deterministic runtime actions where possible:
  - reuse `last_failed_verification_command`
  - derive install targets from structured blocker fields such as `missing_python_module`
- Route shell-based repairs through existing approval policy instead of bypassing approval
- Ensure blocked reasons are explicit:
  - approval required
  - unrepairable blocker
  - budget exhausted
- Preserve existing final-text merge behavior so the agent keeps the useful result summary and appends the repair or verification outcome

### Exit Criteria

- a missing dependency during hard verification is treated as repairable
- repaired turns rerun strong verification before finalizing
- the runtime does not loop indefinitely on repair

## Phase 5: Regression Coverage, Eval Alignment, And Observability

### Objectives

- lock in the new routing behavior
- make phase transitions and blocker classification inspectable
- keep eval reporting aligned with the new verdict model

### Tasks

- Add regression tests for:
  - `none` bypass behavior
  - `soft` read-only evidence follow-up behavior
  - `hard` repair routing on missing Python module failures
  - repair budget exhaustion
  - approval-required repair blockers
  - original-goal extraction ignoring runtime follow-up prompts
- Update eval evidence capture to record:
  - completion mode
  - reviewer verdict
  - next phase
  - repair attempt count
  - verify attempt count
  - blocked reason class
- Update checkpoint or reflection metadata so completion packets and routing decisions are inspectable in traces
- Replay known bad sessions:
  - weak-verification loop cases
  - task-misaligned final answer cases
  - dependency-missing verification failures

### Exit Criteria

- new completion modes are covered by tests
- traces show why the runtime finalized, repaired, verified, or blocked
- eval failures can distinguish verification gaps from repairable blockers

## Suggested Implementation Order

1. activate `soft` in `TaskProfile` and runtime routing
2. extend `VerificationPacket` with blocker and repair fields
3. widen reviewer verdicts and add `next_phase`
4. route runtime follow-up through explicit phases instead of one retry prompt
5. add bounded repair and re-verification budgets
6. cover the new flow with tests and eval metadata

## Files And Areas Expected To Change

- `backend/app/runtime/manager.py`
- `backend/app/services/`
  - `task_profile_service.py`
  - `verification_packet_service.py`
  - `verification_reviewer_service.py`
  - `reflection_service.py`
- `backend/tests/`
  - `test_asset_runtime_tools.py`
  - `test_verification_reviewer_service.py`
  - `test_evidence_verifier.py`
- `backend/evals/`

## Risks To Manage

- classifying too many read-only turns as `soft` when `none` is enough
- letting repair prompts become vague and model-shaped instead of structured and bounded
- introducing two competing routing systems during migration
- overfitting blocker extraction to Python module errors and missing other common repairable blockers
- creating repair paths that still cannot proceed because shell approvals remain unresolved

## Definition Of Done

Goal-driven execution is done when Jarvis routes turns through `none`, `soft`, or `hard` completion modes; keeps read-only analytical turns out of hard verification; converts repairable hard-verification failures into one bounded repair-then-verify attempt; stays anchored to the original user goal; and finalizes or blocks with explicit, inspectable reasoning instead of defaulting to generic blocker summaries.
