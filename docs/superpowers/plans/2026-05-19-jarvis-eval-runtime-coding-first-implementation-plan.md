# Jarvis Eval Baseline And Runtime Upgrade Implementation Plan

Date: 2026-05-19
Depends on: `docs/superpowers/specs/2026-05-19-jarvis-eval-runtime-coding-first-design.md`

## Delivery Strategy

Implement the approved direction in six phases:

1. milestone contract and benchmark foundation
2. evaluation runner and first coding task pack
3. coding tool runtime v1
4. real subagent runtime v1
5. stabilization, gates, and regression discipline

The work should run in two coordinated lanes:

- `eval lane`
- `runtime lane`

The eval lane is the source of product truth. The runtime lane is only allowed to claim progress when benchmark results or diagnosability improve.

## Phase 0: Milestone Contract

### Objectives

- Freeze what this phase is trying to prove
- Prevent scope drift into unrelated general-agent domains
- Define the metrics that later benchmark reports must produce

### Tasks

- Write down the primary milestone metrics:
  - coding task pass rate
  - first-pass success rate
  - approval stall rate
  - iteration limit hit rate
  - workspace violation rate
  - recovery success rate
- Freeze the initial benchmark scope to a coding-first pack
- Freeze the runtime scope to:
  - evaluation harness
  - coding tool runtime upgrades
  - real subagent runtime
  - approval and recovery support required by those two items
- Define what counts as:
  - `pass`
  - `partial`
  - `fail`
  - `invalid_run`

### Exit Criteria

- The team agrees on the milestone metrics and labels
- The team agrees that non-coding domain expansion is out of scope for this phase

## Phase 1: Evaluation Foundation

### Objectives

- Build a reproducible benchmark harness
- Make runtime changes measurable at the task level
- Capture enough evidence to explain failures, not just count them

### Tasks

- Add an evaluation directory structure:
  - `backend/evals/tasks/`
  - `backend/evals/runner/`
  - `backend/evals/reports/`
- Define a task spec format with fields for:
  - task identity
  - workspace fixture or source
  - prompt
  - execution mode
  - approval policy
  - time budget
  - expected checks
  - tags
- Implement a runner that can:
  - create a fresh session
  - bind a deterministic workspace or fixture copy
  - submit the task prompt
  - wait for terminal state or timeout
  - collect messages, timeline events, turns, approvals, tool executions, and final diff
- Add an approval policy adapter for:
  - `deny_all_shell`
  - `allow_shell`
  - `allow_shell_if_pattern_matches`
  - `manual_required`
- Implement run result classification into:
  - `pass`
  - `partial`
  - `fail`
  - `invalid_run`
- Implement first-pass failure tagging with the agreed primary taxonomy
- Add report output suitable for:
  - console summary
  - machine-readable JSON
  - historical comparison

### Exit Criteria

- The same task can be rerun under comparable conditions
- Each task run produces enough evidence to diagnose why it passed or failed
- The runner can generate a baseline report for an initial smoke set

## Phase 2: First Coding Task Pack

### Objectives

- Populate the benchmark with real coding work, not synthetic demo prompts
- Establish a stable baseline before large runtime changes begin

### Tasks

- Build an initial `smoke` pack with 6-8 tasks
- Build an initial `core` pack with 12-18 tasks
- Optionally stage a `stretch` pack with 6-10 tasks for later nightly or milestone runs
- Source tasks in this order:
  - recently completed tasks from this repository
  - near-term backlog items
  - reusable coding-agent task templates mapped to this repository
- Encode expected checks for each task:
  - test or command checks where possible
  - file-change constraints
  - workspace constraints
  - artifact expectations
- Add human-review notes only for tasks that cannot yet be automatically judged
- Run the first full baseline and capture:
  - pass distribution
  - first-pass success rate
  - main failure tags
  - common stall patterns

### Exit Criteria

- The repository has a real initial coding benchmark pack
- The team has a baseline report to compare future runtime changes against
- The top benchmark failure tags are known before runtime refactors begin

## Phase 3: Coding Tool Runtime V1

### Objectives

- Shift common coding flows away from generic shell usage
- Give the model better structured primitives for search, read, edit, diff, and verification
- Reduce benchmark failures caused by poor tool choice and brittle edit flows

### Tasks

- Define the first structured coding tool pack
- Add or upgrade the following runtime tools:
  - `search_text`
  - `read_file_range`
  - `apply_patch`
  - `show_status`
  - `show_diff`
  - `run_test`
  - `structured run_command`
- Refactor the runtime so coding-critical flows prefer structured tools over shell fallback
- Add richer output shaping:
  - line-range reads instead of shallow fixed truncation
  - bounded large-output capture
  - explicit truncation and exit-code metadata
- Introduce side-effect-tier approval policy:
  - read-only default allow
  - workspace writes configurable
  - destructive writes strong approval
  - shell execution strong approval
  - external side effects strong approval
- Ensure tool results are logged with enough structure for benchmark diagnosis

### Exit Criteria

- Read, search, diff, and test flows no longer depend on shell as the normal path
- Benchmark failures in `wrong_tool_choice` and `edit_failure` decrease from baseline
- Tool logs are detailed enough to support post-run diagnosis

## Phase 4: Real Subagent Runtime V1

### Objectives

- Turn subagents into durable execution units
- Enable actual parallel or sidecar work rather than synchronous summary generation
- Improve benchmark outcomes on investigation-heavy or multi-thread tasks

### Tasks

- Define and persist a subagent state model:
  - `queued`
  - `running`
  - `waiting_input`
  - `completed`
  - `failed`
  - `cancelled`
- Persist subagent runtime metadata:
  - parent session
  - parent turn when relevant
  - requested role or purpose
  - prompt and follow-up inputs
  - execution workspace
  - isolation mode
  - latest summary
  - produced artifacts
  - tool execution log
- Add first-class runtime operations:
  - `spawn_subagent`
  - `wait_subagent`
  - `send_subagent_input`
  - `cancel_subagent`
- Replace synchronous summary-only subagent flow with durable orchestration
- Implement a phase-one delegation policy that encourages delegation only for:
  - independent investigation
  - side work that does not block the next local step
  - parallel verification or evidence gathering
- Implement or refine isolation rules for:
  - `shared`
  - `worktree`
- Ensure lead-agent prompting and runtime behavior agree on when delegation is appropriate

### Exit Criteria

- The lead agent can spawn a subagent, continue local work, and later integrate results
- Subagent execution is durable and diagnosable
- Benchmark tasks that benefit from independent investigation show measurable improvement

## Phase 5: Stabilization And Gates

### Objectives

- Convert improvement spikes into repeatable discipline
- Catch regressions early
- Make benchmark review part of the normal development loop

### Tasks

- Add `pre-merge smoke` benchmark execution for critical runtime changes
- Add `nightly core` benchmark execution
- Add a milestone `full-run` report
- Add weekly failure-tag review:
  - which tags dropped
  - which tags rose
  - which runtime changes caused the shift
- Patch the highest-value regressions in:
  - approval handling
  - recovery flows
  - iteration-limit behavior
  - benchmark reporting gaps

### Exit Criteria

- Improvements remain visible across repeated runs, not only on one-off samples
- Regressions are visible quickly
- Most failures land in a compact, stable taxonomy

## Suggested Implementation Order

1. Freeze milestone metrics and labels
2. Build the eval runner and result model
3. Add the first smoke and core task packs
4. Capture and review the baseline
5. Deliver structured coding tool runtime upgrades
6. Re-run benchmark and confirm target failure tags move
7. Deliver real subagent runtime operations and persistence
8. Re-run benchmark with investigation-heavy tasks
9. Add gates, nightly runs, and stabilization fixes

## Expected Files And Areas To Change

### Evaluation Lane

- `backend/evals/tasks/`
- `backend/evals/runner/`
- `backend/evals/reports/`
- `backend/tests/` for eval-related verification where appropriate

### Runtime Lane

- `backend/app/runtime/manager.py`
- `backend/app/tools/broker.py`
- `backend/app/services/tool_service.py`
- `backend/app/services/subagent_service.py`
- `backend/app/services/approval_service.py`
- `backend/app/services/checkpoint_service.py`
- `backend/app/services/turn_service.py`
- `backend/app/mcp/registry.py` where tool surface changes affect registration
- `backend/app/schemas/` for any new tool, approval, or subagent payloads
- `frontend/src/lib/api.ts` and UI surfaces only where needed to expose benchmark or runtime controls

## Risks To Manage During Implementation

- building benchmark infrastructure that is hard to maintain before it becomes useful
- adding tools faster than benchmark success improves
- making subagent orchestration elaborate without improving task outcomes
- letting shell remain the real default path despite new structured tools
- expanding into broader general-agent domains before the coding-first loop is stable

## Definition Of Done

This implementation plan is complete when:

- Jarvis has a usable coding-task benchmark harness with reproducible runs
- the benchmark reports stable pass, partial, fail, and invalid classifications
- the runtime offers a real structured coding tool pack for search, read, patch, diff, and verification
- subagents are durable execution units with spawn, wait, send-input, and cancel flows
- runtime changes are reviewed against benchmark movement, not only local intuition
- pre-merge or nightly benchmark gates exist for the critical paths introduced in this phase
