# Jarvis Lightweight Verification Reviewer Implementation Plan

Date: 2026-05-25
Depends on: `docs/superpowers/specs/2026-05-25-jarvis-lightweight-verification-reviewer-design.md`

## Delivery Strategy

Implement the lightweight verification reviewer in four phases:

1. reviewer packet assembly and hard guardrail extraction
2. prompt-driven reviewer and one-shot verification retry
3. old verifier branch removal and runtime simplification
4. regression, eval alignment, and observability polish

The plan intentionally keeps the existing task-scoped runtime and only simplifies the acceptance layer. The goal is not to redesign the whole agent loop again. The goal is to replace the current rule-heavy completion logic with a smaller review boundary.

## Phase 1: Reviewer Packet Assembly And Hard Guardrail Extraction

### Objectives

- define the compact review packet as the single reviewer input
- preserve only the minimum runtime guardrails needed for safety
- stop internal follow-up prompts from polluting original-goal inference

### Tasks

- Add a dedicated reviewer packet builder service or module
- Assemble these explicit reviewer fields:
  - `original_goal`
  - `current_result_summary`
  - `artifact_summary`
  - `evidence_summary`
  - `open_verification_gaps`
  - `uncertainty_already_stated`
  - `remaining_auto_verify_attempts`
- Move original-goal extraction into a stricter helper:
  - derive from the task root or durable task summary
  - exclude runtime-generated follow-up prompts
- Keep only the thin hard guardrails outside the reviewer:
  - original-goal anchoring
  - one automatic verification retry maximum
  - repeated weak-verification stall detection
  - forced user-facing uncertainty on blocked completion
- Convert existing weak-verification loop detection into packet input rather than direct verifier branching

### Exit Criteria

- the runtime can build one stable reviewer packet for a finishing turn
- original-goal extraction is no longer polluted by internal follow-up prompts
- weak-verification stall state is represented explicitly in the packet

## Phase 2: Prompt-Driven Reviewer And One-Shot Verification Retry

### Objectives

- replace most current verifier branching with a prompt-driven review step
- support exactly one targeted automatic verification retry
- keep retry prompts narrow enough to converge instead of wandering

### Tasks

- Add a reviewer prompt template based on the approved design questions:
  - original goal
  - goal satisfaction
  - evidence-backed claims
  - unsupported claims
  - whether one more targeted verification step is justified
  - whether uncertainty must be surfaced instead
- Define a structured reviewer response contract:
  - `done`
  - `continue_with_verification`
  - `blocked_uncertain`
  - plus `goal_assessment`, `supported_claims`, `unsupported_claims`, `next_verification_action`, and `user_visible_uncertainty`
- Add runtime validation for reviewer output
- Wire reviewer results into the completion boundary:
  - `done` finalizes the turn
  - `continue_with_verification` injects one targeted verification follow-up and decrements retry budget
  - `blocked_uncertain` stops the loop and finalizes with explicit uncertainty
- Enforce one-shot retry semantics:
  - no more than one automatic verification retry per turn
  - if retry budget is exhausted, the reviewer cannot continue the loop again
- Narrow verification follow-up prompts so they describe one concrete action instead of generic "continue verifying" instructions

### Exit Criteria

- finishing turns pass through the new reviewer step
- the runtime can perform one targeted extra verification attempt
- the reviewer cannot create open-ended retry loops

## Phase 3: Old Verifier Branch Removal And Runtime Simplification

### Objectives

- retire the current rule-heavy acceptance logic as the primary path
- keep only the minimum useful evidence summarization utilities
- reduce tool-shaped completion branching

### Tasks

- Remove or demote the current primary role of:
  - `wrong_tool_choice`
  - `missing_verification`
  - `weak_external_evidence`
  - similar detailed branch taxonomies as direct control-flow outputs
- Keep only evidence extraction helpers that still improve reviewer packet quality:
  - verification-state summarization
  - artifact detection
  - uncertainty detection
  - weak-verification repetition detection
- Replace tool-specific task semantics such as "requires web_search" with obligation-oriented packet fields
- Simplify `_run_reflection()` so it orchestrates:
  - packet assembly
  - reviewer call
  - structured verdict validation
  - retry or stop behavior
- Ensure blocked finalization uses `user_visible_uncertainty` rather than generic runtime stop messages

### Exit Criteria

- the new reviewer path is the default completion path
- the old verifier taxonomy no longer drives most finalization decisions
- runtime completion code is simpler and easier to inspect

## Phase 4: Regression, Eval Alignment, And Observability

### Objectives

- lock in the new lightweight reviewer behavior
- verify that the simplified acceptance layer still blocks known bad cases
- make reviewer outcomes easy to diagnose

### Tasks

- Add regression coverage for:
  - original-goal extraction ignoring runtime follow-up prompts
  - one automatic verification retry maximum
  - repeated weak verification ending in `blocked_uncertain`
  - task-misaligned final answers being rejected
  - blocked outputs surfacing explicit user-visible uncertainty
- Update eval evidence capture to record:
  - reviewer verdict
  - retry count
  - stalled verification tag
  - whether uncertainty was required
- Update checkpoint or reflection metadata so reviewer packet and reviewer verdict are inspectable in traces
- Replay known bad sessions:
  - task-misaligned final answer cases
  - repeated weak verification loop cases

### Exit Criteria

- known loop and false-completion cases no longer regress
- reviewer decisions are visible in traces and durable state
- the simplified reviewer still protects high-risk turn completion

## Suggested Implementation Order

1. Add reviewer packet builder and strict original-goal extraction
2. Add lightweight reviewer prompt and structured response parsing
3. Wire one-shot verification retry into the completion boundary
4. Add blocked-uncertainty finalization behavior
5. Remove or demote old verifier branch taxonomy
6. Add regressions and eval coverage for known bad cases

## Files And Areas Expected To Change

- `backend/app/runtime/manager.py`
- `backend/app/services/`
  - `task_profile_service.py`
  - `verification_packet_service.py`
  - `evidence_verifier.py`
  - `reflection_service.py`
- `backend/app/services/checkpoint_service.py`
- `backend/evals/`
- `backend/tests/`

## Risks To Manage

- making the reviewer packet too thin and forcing the model to infer what the runtime should already know
- allowing the one retry budget to be consumed by a low-value verification step
- leaving too much old verifier logic in place and ending up with two overlapping acceptance systems
- stopping too aggressively and degrading useful automatic verification

## Definition Of Done

The lightweight verification reviewer is done when Jarvis finalizes turns through a prompt-driven review packet anchored to the original task goal, can perform at most one targeted automatic verification retry, terminates repeated weak verification in `blocked_uncertain`, forces user-visible uncertainty when proof remains insufficient, and no longer relies on the current large rule-heavy verifier taxonomy as the main completion gate.
