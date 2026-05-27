# Jarvis Evidence-Based Reflection And Completion Gate Design

## Summary

Upgrade Jarvis from a prompt-shaped Reflection gate into a typed, evidence-based completion system that decides turn outcomes from user obligations, execution evidence, and completion claims instead of from final-answer language alone.

This design has two linked goals:

1. define a general-purpose completion framework that works across coding, retrieval, document, automation, and side-effect tasks
2. map that framework onto Jarvis through a staged runtime upgrade that first eliminates obvious false completions and then grows into a reusable verifier architecture

The intended product direction is not "more reflection." It is "harder to fake completion."

## Goal

Jarvis should stop treating "the model said it is done" as equivalent to "the task is done."

The runtime should instead:

- infer what the user expects to be true when the turn completes
- collect structured evidence from execution
- detect when the assistant is claiming those expectations are satisfied
- allow `done` only when critical obligations are supported by sufficient evidence

## Problem

Jarvis currently has a structured Reflection decision in the lead runtime, but it still behaves as a rule-based language gate rather than as a true acceptance system.

Three product problems follow:

- completion remains too close to final-text semantics
- verification remains tool-shaped instead of obligation-shaped
- reflection has no explicit obligation, claim, and evidence model

That leaves the runtime vulnerable to false completions such as:

- editing `requirements.txt` and calling that dependency installation
- using `py_compile` and calling that runtime validation
- succeeding in system Python and calling that project-environment success
- running any `run_test` command and calling that verification

## Scope

This design covers:

- verification-scope control so not every task is forced through hard validation
- an obligation, claim, and evidence data model
- verdict semantics for `done`, `continue`, `repair`, `blocked`, and optional `uncertain`
- runtime integration in Jarvis
- a staged rollout plan for Jarvis

## Non-Goals

This design does not include:

- a second model call on every loop iteration
- replacing the lead agent with a separate verifier agent
- forcing hard verification on purely informational turns
- solving every domain-specific validator in the first phase

## Core Invariant

The completion question should shift from:

- "Does the answer look finished?"

to:

- "What obligations did the user ask the system to satisfy, which completion claims is the assistant making, and is there enough independent evidence to support those claims?"

This yields the main invariant:

> `done` is an acceptance state, not a language state.

## Verification Scope

Not all tasks should be verified equally.

The runtime should assign one of three verification levels before strong completion gating runs:

- `none`
  - no completion gate
  - used for explanation, brainstorming, translation, summarization, and similar informational output
- `soft`
  - lightweight consistency checks only
  - used when the turn is mostly informational but still benefits from basic alignment checks
- `hard`
  - evidence-based completion gate
  - used when the assistant is delivering a result that can fail in the world

### When Hard Verification Applies

Hard verification should run when the assistant is delivering or implying a claim in one of these categories:

- `artifact_state`
- `environment_state`
- `behavior_runtime`
- `external_fact`
- `side_effect_completion`

Purely informational analysis normally stays outside hard verification.

## Detecting Completion Claims

Jarvis should not use keyword matching as the primary trigger.

The correct trigger is a three-way match across:

1. `request obligations`
2. `execution actions`
3. `completion claims`

In practice:

- user requests define what the turn is supposed to make true
- execution actions show what the agent actually did
- final claims show what result the agent is asserting or implying

When those three align around a claim that can fail in the world, hard verification should run.

## Core Data Model

The verifier should not consume only raw conversation text. It should consume a structured verification packet with four main sections:

- `task_profile`
- `obligations`
- `claims`
- `evidence`

### Task Profile

`task_profile` describes the turn at a coarse level:

- task kinds
- verification level
- risk level

It does not decide completion by itself. It provides the routing context for verification.

### Obligations

`obligations` represent what the user actually asked the system to make true.

Examples:

- a dependency is installed in the target environment
- a script is runnable in that environment
- a document is exported to a target path
- an external fact is confirmed with sufficiently fresh evidence
- a side effect really happened in the target system

An obligation may be marked `critical`. Any critical obligation that is not satisfied blocks `done`.

### Claims

`claims` represent what the assistant is asserting in the final answer, either explicitly or implicitly.

Examples:

- "installed"
- "fixed"
- "runnable"
- "latest confirmed"
- "sent"

Claims should be mapped back to the obligations they cover.

### Evidence

`evidence` represents independently observed execution facts.

Evidence may come from:

- tool calls
- command outputs
- file diffs
- environment probes
- external API responses
- state read-back after side effects

Evidence is the only thing that can support `done`.

## Assertion Taxonomy

The verifier should classify obligations and claims using a small cross-domain assertion taxonomy.

### `artifact_state`

The turn claims that an artifact now exists or has been changed.

Examples:

- file created
- config updated
- document exported

Typical minimum evidence:

- successful write or export result
- path existence or diff visibility

### `environment_state`

The turn claims that an environment has been configured correctly.

Examples:

- dependency installed
- config written to the target environment

Typical minimum evidence:

- successful configuration action
- confirmed target environment identity
- probe inside that same environment

### `behavior_runtime`

The turn claims that a program, script, service, or interface now behaves correctly enough to run.

Examples:

- script runs
- service responds
- command succeeds

Typical minimum evidence:

- actual execution in the target environment
- success return status
- minimal runtime output or behavior signal

### `external_fact`

The turn claims that an external fact has been verified.

Examples:

- today's score
- current CEO
- latest price

Typical minimum evidence:

- external source support
- freshness adequate for the user request
- evidence quality above the domain threshold

### `side_effect_completion`

The turn claims that an external action truly happened.

Examples:

- message sent
- issue created
- document uploaded

Typical minimum evidence:

- external system success response
- object id, revision id, receipt, or similar durable handle
- optional read-back when feasible

## Evidence Grading

Evidence should be graded per obligation, not globally.

Recommended first-stage strengths:

- `none`
- `weak`
- `sufficient`
- `conflicting`

Typical examples:

- `requirements.txt` updated for a package requirement
  - supports environment setup intent
  - usually only `weak` for actual installation
- package successfully imported inside the target project environment
  - usually `sufficient` for dependency presence
- package present in system Python while missing in the target `.venv`
  - `conflicting` for "installed in target environment"

`weak` evidence must not be enough to satisfy high-risk obligations.

## Verdict Semantics

Per-obligation verdicts should use:

- `satisfied`
- `missing_evidence`
- `conflicting_evidence`
- `blocked`

Turn-level verdicts should use:

- `done`
- `continue`
- `repair`
- `blocked`
- optional `uncertain`

### `done`

Allowed only when all critical obligations are `satisfied`.

### `continue`

Used when the result may still be valid, but required evidence is missing and the runtime knows how to gather it.

### `repair`

Used when the current result has already been contradicted by evidence and the agent needs to fix the result before verifying again.

### `blocked`

Used when the runtime cannot legally or practically close the loop.

Typical causes:

- missing approval
- missing credentials
- missing user input
- missing target resource
- sandbox or network restrictions that prevent required verification

### `uncertain`

Allowed only for domains that explicitly permit uncertainty. It should not be used for code changes, environment setup, or side-effect completion.

## Runtime Flow

The recommended execution chain is:

`Profile -> Act -> Collect -> Verify -> Repair -> Re-verify -> Finalize`

### Profile

Build a task profile at turn start:

- task kinds
- verification level
- initial obligations
- risk level

### Act

Run the normal actor loop. This design does not remove ReAct behavior or tool calling.

### Collect

After each tool call, persist both:

- the original transcript artifact
- structured evidence records that can later feed verification

### Verify

When the actor wants to stop, the runtime should:

- extract completion claims
- assemble a verification packet
- run the evidence verifier
- receive a structured turn verdict plus per-obligation results

### Repair

If the verdict is `continue` or `repair`, the runtime should feed targeted repair instructions back into the actor loop instead of relying on a vague follow-up prompt.

### Finalize

Only `done` may finalize as completed.

`blocked` must finalize with a user-readable blocker.

## Jarvis Mapping

This design should not be implemented by making `_run_reflection()` more verbose.

Instead, the current Reflection boundary in [backend/app/runtime/manager.py](/Users/bytedance/Desktop/python/Jarvis/backend/app/runtime/manager.py:2969) should become an orchestration point for:

- task profiling
- verification packet assembly
- evidence verification
- repair planning

Recommended new services:

- `backend/app/services/task_profile_service.py`
- `backend/app/services/verification_packet_service.py`
- `backend/app/services/evidence_verifier.py`
- `backend/app/services/repair_planner.py`

### Immediate Risk To Remove

The current runtime should stop treating:

- any `run_test` call as verification
- environment actions in the wrong interpreter as successful target-environment setup
- dependency declaration edits as dependency installation

Those are the shortest paths to false completion.

## Staged Rollout

### Phase 1: Stop The Bleeding

Primary goal:

- reduce obvious false completions quickly

Required changes:

- restrict `run_test` so it cannot act as a generic side-effect executor
- distinguish verification commands from installation or mutation commands
- add a minimal verification-scope resolver
- add assertion-level checks for high-risk cases:
  - code change
  - dependency install
  - environment setup
  - external fact lookup
- introduce `repair` as a first-class verdict

Success in this phase means Jarvis no longer reports the class of bad case where work was declared complete in the wrong environment or with only weak evidence.

### Phase 2: Introduce A Reusable Verifier Layer

Primary goal:

- replace scattered completion heuristics with a unified verification architecture

Required changes:

- add task profiling
- add structured evidence collection
- assemble verification packets at completion boundaries
- drive completion with per-obligation verdicts

### Phase 3: Expand Domain Coverage

Primary goal:

- extend the same framework beyond code-first tasks

Likely domains:

- document creation and export
- side-effect automation flows
- richer external-fact validation
- future multi-step desktop or SaaS operations

## Testing And Metrics

The rollout should be measured at three levels.

### Unit Tests

Test `obligation -> evidence -> verdict` directly.

### Trace Replay

Replay real bad cases using stored messages, tool executions, checkpoints, and final text.

The known session `575a9f67-aa84-4047-90ef-3a0f0563880d` should become a permanent regression case.

### Product Metrics

Track:

- first-pass completion rate
- false-done rate
- repair recovery rate
- blocked accuracy
- verification coverage rate

The most important product pair is:

- increase first-pass completion rate
- decrease false-done rate

## Acceptance Criteria

This design should be considered successful only when all of the following are true:

- Jarvis no longer treats completion as a language-only decision
- hard verification runs only for tasks that actually make failure-bearing completion claims
- `done` is impossible when any critical obligation lacks sufficient evidence
- false completions from wrong environment, weak runtime validation, or side-effect disguise are materially reduced
- the verifier architecture is reusable across more than one task domain

## Notes On Relationship To Current Reflection Design

The existing runtime reflection design is still useful as a narrow completion-boundary checkpoint.

This document extends that direction by changing the unit of judgment:

- from tool and prompt heuristics
- to obligations, claims, evidence, and acceptance verdicts

In short:

- current design: structured reflection
- this design: structured reflection plus evidence-based acceptance
