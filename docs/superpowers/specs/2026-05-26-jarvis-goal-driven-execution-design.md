# Jarvis Goal-Driven Execution Design

Date: 2026-05-26
Status: Approved in conversation, spec written for review

## Summary

Jarvis should move from a verification-shaped loop to a goal-driven completion system with three levels:

- `none`
- `soft`
- `hard`

The runtime should first decide what kind of completion claim the turn is making, what evidence level is appropriate, and whether any blocker is repairable inside the current runtime.

## Goals

- avoid hard verification on ordinary conversation
- add a real `soft` layer for read-only but conclusion-bearing turns
- let hard-completion turns repair fixable blockers before giving up
- keep routing anchored to the original user goal
- keep execution bounded and convergent

## Non-Goals

This design does not include:

- a universal planner for every turn
- unlimited self-healing loops
- silent bypass of shell approvals
- domain-perfect repair logic in the first phase

## Problem

Jarvis currently behaves too much like:

- do work
- try one verification step
- if verification fails, summarize the blocker

That causes failures such as a coding turn running `python3 simple_mnist.py ...`, hitting `ModuleNotFoundError: No module named 'numpy'`, and stopping at "missing numpy" instead of deciding whether to repair and re-verify.

## Product Decision

Adopt a three-level completion system:

- `none` for ordinary conversational or purely informational turns
- `soft` for read-only but conclusion-bearing turns
- `hard` for turns that claim a result that can fail in the world

Within `hard`, replace "verify or block" with a bounded goal-driven flow:

1. `work`
2. `assess`
3. `repair` if the blocker is fixable
4. `verify`
5. `finalize`

## Level Rules

### `none`

Use for ordinary conversation, explanation, translation, rewriting, and summarization of user-provided content.

Behavior:

- do not run a completion reviewer
- require only a non-empty, non-misaligned final answer

### `soft`

Use for read-only but conclusion-bearing turns such as repository summaries, bug-location hypotheses, and architecture explanations grounded in workspace inspection.

Behavior:

- run a lightweight completion reviewer
- allow only read-only follow-up actions
- allow uncertainty correction or answer alignment fixes
- do not allow environment mutation or side-effect repair

### `hard`

Use for code changes, dependency installation, runnable script or service claims, exported artifact guarantees, and fresh external fact lookup.

Behavior:

- run a goal-driven reviewer
- allow one bounded repair attempt when the blocker is fixable
- require re-verification after repair
- stop with explicit uncertainty or blocking reason when repair is impossible, approval is required, or budget is exhausted

## Runtime State Machine

The runtime should follow:

1. `work`
2. `assess`
3. `gate:none | gate:soft | gate:hard`
4. `repair` when `hard` sees a fixable blocker
5. `verify`
6. `finalize`

Detailed meaning:

- `work`: the agent reads, writes, and uses tools to produce a candidate result
- `assess`: runtime derives completion level, evidence, blockers, and repairability
- `gate:none`: finalize directly
- `gate:soft`: run a read-only completion reviewer
- `gate:hard`: run a goal-driven reviewer
- `repair`: only for `hard`, and only when the blocker is fixable
- `verify`: run strong task-relevant validation
- `finalize`: return `done`, `done_with_uncertainty`, or `blocked`

Blocked states should be explicitly classified into:

- `blocked_requires_approval`
- `blocked_unrepairable`
- `blocked_budget_exhausted`

## Data Model Changes

### `TaskProfile`

Keep `task_kinds`, `verify_level`, and `obligations`, and add `completion_mode`:

- `direct` for `none`
- `evidence_check` for `soft`
- `goal_driven` for `hard`

### `VerificationPacket`

The existing packet can keep its name, but it should act like a completion packet. Add:

- `candidate_result_summary`
- `blockers`
- `repairable_blockers`
- `last_failed_action`
- `last_failed_verification_command`
- `remaining_repair_attempts`
- `remaining_verify_attempts`

`repairable_blockers` should be structured. Example kinds:

- `missing_python_module`
- `wrong_python_environment`
- `missing_entrypoint_dependency`
- `approval_required_for_shell`

### `ReviewResult`

Recommended verdicts:

- `done`
- `done_with_uncertainty`
- `continue_with_read_only_evidence`
- `continue_with_repair`
- `continue_with_verification`
- `blocked`

### `ReflectionDecision`

Keep it lightweight, but add `next_phase`:

- `finalize`
- `gather_evidence`
- `repair`
- `verify`
- `blocked`

## Reviewer Contracts

### `soft` Reviewer

Inputs:

- `original_goal`
- `candidate_result`
- `read_only_evidence`
- `uncertainty_already_stated`

Allowed outcomes:

- `done`
- `done_with_uncertainty`
- `continue_with_read_only_evidence`

### `hard` Reviewer

Inputs:

- `original_goal`
- `candidate_result`
- `evidence`
- `blockers`
- `repairable_blockers`
- `remaining_repair_attempts`
- `remaining_verify_attempts`

Allowed outcomes:

- `done`
- `done_with_uncertainty`
- `continue_with_repair`
- `continue_with_verification`
- `blocked`

Rule:

- if the blocker is repairable and budget remains, route to `repair`
- if no blocker remains but evidence is still weak, route to `verify`
- if repair is impossible, approval is required, or budget is exhausted, route to `blocked`

## Example

If `python3 simple_mnist.py ...` fails with `ModuleNotFoundError: No module named 'numpy'`, the runtime should not stop at "missing numpy". It should classify the blocker as `missing_python_module`, decide whether repair is allowed, install the dependency if policy permits, and then rerun the same verification command.

## Migration

Recommended order:

1. implement real `soft` routing in `TaskProfile`
2. extend `VerificationPacket` with blocker and repair fields
3. widen `ReviewResult` and `ReflectionDecision` to support `repair`
4. change the runtime follow-up path from "continue verifying" to phase-based routing
5. add one bounded repair budget and one bounded re-verification budget

## Acceptance Criteria

- ordinary chat and purely informational turns do not enter hard verification
- read-only analytical turns use `soft`, not `hard`
- code-change and dependency-install turns can route from failed verification into bounded repair
- the runtime remains anchored to `original_goal`
- a missing dependency during hard verification is treated as a repairable blocker instead of an immediate terminal summary
