# Jarvis Lightweight Verification Reviewer Design

Date: 2026-05-25
Status: Approved in conversation, spec written for review

## Summary

Simplify Jarvis verification from a growing rule-heavy verifier into a lightweight hybrid review step that runs after the agent believes it is ready to finish.

The new design keeps only a small number of non-negotiable runtime guardrails and moves the main judgment into a prompt-driven reviewer. Instead of maintaining many task-specific branching rules such as "wrong tool choice" versus "missing verification" versus "weak external evidence" as primary control flow, the runtime assembles a compact review packet and asks a final reviewer:

- what was the user's original goal
- whether the current result really satisfies that goal
- which conclusions are evidence-backed
- which conclusions remain inferred or unverified
- whether one targeted extra verification step is worth attempting
- whether the system must stop and communicate uncertainty instead

This design preserves two product requirements:

- Jarvis may automatically perform one extra targeted verification step before final output
- Jarvis must not loop indefinitely when verification remains weak or repetitive

## Goal

Replace the current overly complicated verification and reflection logic with a smaller, more stable acceptance layer that:

- stays anchored to the original task goal
- uses a prompt-driven reviewer for most acceptance judgment
- allows at most one automatic follow-up verification attempt
- stops repeated weak verification loops
- forces explicit user-facing uncertainty when proof is insufficient

## Non-Goals

This design does not include:

- a second independent verifier model
- full removal of all hard runtime safeguards
- broad task taxonomy expansion
- replacing task-scoped runtime or task routing
- changing frontend interaction surfaces in this phase

## Problem

The current verification path has become too tool-shaped and too branch-heavy.

Three concrete problems follow:

1. **Tool names leak into task semantics**  
   Runtime logic asks whether the task "requires web_search" instead of asking whether the task requires fresh external evidence.

2. **Internal follow-up prompts can pollute later judgment**  
   Reflection and completion inference can drift away from the original user task because runtime-generated follow-up prompts remain in the message stream.

3. **Weak verification can loop instead of converging**  
   When a task keeps producing only weak evidence, the verifier can repeatedly return `continue` without changing the verification strategy enough to terminate.

The system needs a smaller acceptance model that is easier to reason about and harder to wedge into loops.

## Approaches Considered

### Approach A: Pure Prompt Reviewer

After execution, ask the model another question using a reviewer prompt and let that answer fully determine whether the task is done.

This is appealingly simple, but too fragile by itself. It still lets the system self-justify weak conclusions and has no reliable loop brake.

### Approach B: Lightweight Hybrid Reviewer

Use a prompt-driven reviewer as the primary acceptance mechanism, but keep a few hard runtime guardrails:

- original-goal anchoring
- one automatic verification retry maximum
- repeated weak-verification stall detection
- forced uncertainty on blocked output

This is the recommended approach.

### Approach C: Keep The Current Structured Verifier And Continue Refining Rules

Continue growing the current verifier and reflection rules until they cover more cases.

This increases complexity faster than reliability. The system already shows signs of overfitting to tool names and failure reason taxonomies.

## Product Decision

Choose Approach B.

Jarvis should move to a lightweight hybrid reviewer that uses prompt-based acceptance judgment inside a tightly bounded runtime shell.

## Core Invariants

The new reviewer flow must preserve these invariants:

1. The review step is anchored to the original task goal, not to runtime-generated follow-up prompts.
2. The reviewer may request at most one additional automatic verification attempt.
3. Repeated weak verification must terminate in `blocked_uncertain`, not in repeated `continue`.
4. User-visible uncertainty is mandatory when proof remains insufficient.

## Reviewer Model

### Reviewer Responsibilities

The reviewer has exactly three allowed outcomes:

- `done`
- `continue_with_verification`
- `blocked_uncertain`

It does not own task routing, task memory, or tool execution policy. It only judges whether the current result is ready, whether one more targeted verification attempt is worthwhile, or whether the system must stop and communicate uncertainty.

### Reviewer Input Packet

The reviewer should not consume the whole message transcript. The runtime should assemble a compact packet with these fields:

- `original_goal`
- `current_result_summary`
- `artifact_summary`
- `evidence_summary`
- `open_verification_gaps`
- `uncertainty_already_stated`
- `remaining_auto_verify_attempts`

#### `original_goal`

This must come from the current task's original user request or durable task summary.

It must not be reconstructed from runtime follow-up prompts.

#### `current_result_summary`

This is the user-facing result Jarvis is about to return if the turn ends now.

#### `artifact_summary`

This is a compact summary of files changed or artifacts produced.

Examples:

- `simple_crawler.py created`
- `requirements.txt updated`
- `report.pdf exported`

#### `evidence_summary`

This is the compact summary of meaningful verification evidence gathered so far.

Examples:

- `python3 -m py_compile simple_crawler.py -> exit_code=0`
- `run_test classified as syntax_check, evidence_strength=weak`
- `web_search returned weak evidence`

#### `open_verification_gaps`

This is a runtime-generated description of the remaining proof gaps.

Examples:

- only syntax validation was run
- the main execution path has not been exercised
- the dependency was not verified in the target environment
- the external fact lacks fresh supporting evidence
- repeated weak verification indicates the process is stalled

#### `remaining_auto_verify_attempts`

This must be an explicit integer and in the first version it should only be `1` or `0`.

### Reviewer Prompt

The review prompt should be built around these questions:

1. What is the user's original goal?
2. Does the current result fully satisfy that goal?
3. Which key conclusions are supported by evidence?
4. Which claims remain inferred, weakly supported, or unverified?
5. If there is a key verification gap, is exactly one additional targeted verification attempt likely to resolve it?
6. If not, the output must explicitly communicate uncertainty instead of pretending certainty.
7. If code changed, is there enough evidence to support saying it is runnable?

The prompt should instruct the reviewer to prefer caution over confident completion.

### Reviewer Output Contract

The reviewer must return structured output with:

- `verdict`
- `goal_assessment`
- `supported_claims`
- `unsupported_claims`
- `next_verification_action`
- `user_visible_uncertainty`

Rules:

- `next_verification_action` is required only for `continue_with_verification`
- `user_visible_uncertainty` is required only for `blocked_uncertain`
- `verdict=done` is not allowed if unsupported claims include a critical task requirement

## Runtime Integration

### Review Position In The Turn

The reviewer runs after the main agent has produced a candidate final result and before the runtime publishes that result as final.

The order should be:

1. main agent execution reaches a candidate final result
2. runtime assembles the reviewer packet
3. reviewer returns one of three verdicts
4. runtime either:
   - finalizes the turn
   - injects one targeted verification follow-up
   - stops with explicit uncertainty

### Automatic Verification Retry

`continue_with_verification` is allowed only when:

- the missing proof is important
- the gap appears resolvable with one concrete additional action
- `remaining_auto_verify_attempts > 0`

The follow-up prompt must be narrow and action-oriented.

It should not say vague things such as:

- continue verifying
- check again
- inspect more

It should say one concrete action such as:

- run the script on a small real input and confirm the expected output fields
- verify the dependency from the target environment interpreter
- rerun the command with a stronger runtime check than syntax validation

### Retry Budget

Each turn gets at most one automatic verification retry.

If the reviewer still sees unresolved critical gaps after that retry, the next verdict must be `blocked_uncertain`.

### Final Blocked Behavior

When the reviewer returns `blocked_uncertain`:

- the runtime must stop the loop
- the runtime must not ask the agent to keep trying
- the final user-visible output must explicitly describe the uncertainty or missing proof

## Minimal Hard Guardrails

The design intentionally keeps a small number of non-model runtime rules:

### 1. Original Goal Anchoring

The system must always preserve the original task goal outside the model and inject it into the reviewer packet directly.

### 2. One Retry Maximum

The runtime must never grant more than one automatic verification retry per turn.

### 3. Repeated Weak Verification Stall Detection

If the runtime observes repeated weak verification attempts of the same class, it should mark the review packet as stalled.

Examples:

- repeated syntax-only checks
- repeated package probes in the wrong environment
- repeated low-value verification that does not strengthen evidence

In that case, the reviewer must not return `continue_with_verification`.

### 4. Forced User-Facing Uncertainty

If proof remains insufficient after the allowed retry budget is exhausted, the runtime must require explicit uncertainty language in the final output.

## Suggested First Implementation

Start with a narrow cutover:

1. keep task-scoped runtime and routing as-is
2. replace most current reflection/verifier branching with the new reviewer packet and prompt
3. preserve only the four hard guardrails above
4. keep existing tool-result extraction and evidence summarization, but simplify the acceptance logic

This avoids rewriting the entire runtime while still removing most of the current rule sprawl.

## Risks

- making the reviewer packet too vague and forcing the model to reconstruct missing structure
- under-specifying `next_verification_action`, causing the retry step to become open-ended again
- allowing blocked tasks to continue because uncertainty language is not enforced strongly enough
- removing too many hard checks and reintroducing false completions

## Acceptance Criteria

The lightweight reviewer design is successful when:

- original task goals are no longer polluted by runtime follow-up prompts
- tasks that need more proof get at most one automatic targeted verification retry
- repeated weak verification stops in `blocked_uncertain` instead of looping
- final blocked outputs clearly state uncertainty or missing proof
- the runtime no longer depends on a large branching verifier taxonomy to decide whether to finish
