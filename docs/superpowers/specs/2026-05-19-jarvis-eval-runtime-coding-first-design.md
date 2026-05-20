# Jarvis Eval Baseline And Runtime Upgrade Design

## Goal

Upgrade Jarvis into a stronger general-purpose local agent runtime by using coding tasks as the first hard validation domain.

This phase has two primary goals:

1. establish a task-level evaluation baseline that can prove whether Jarvis is getting better or worse
2. upgrade the execution runtime so Jarvis completes real coding tasks more reliably while keeping the core architecture domain-agnostic

The intended product direction remains a generalist desktop agent. This phase is coding-first, not coding-only.

## Problem

Jarvis already has important production-oriented building blocks:

- durable turns and background jobs
- approval and recovery primitives
- workspace binding and branch-aware session context
- subagent worktree isolation
- session memory and context assembly
- MCP integration and local tool registration

But it still falls short of production-grade agents such as Codex or Claude Code in two critical ways.

### Missing Product Truth Loop

Jarvis does not yet have a durable task benchmark harness that can answer:

- whether the agent actually completed a real task
- which failure mode caused a miss
- whether a runtime change improved or regressed performance
- whether gains are stable or accidental

Without this loop, agent iteration is guided too much by intuition and isolated anecdotes.

### Runtime Is Still Too Generic For High-Success Coding Work

The current runtime is flexible, but not yet shaped for high-success execution on complex repository tasks:

- coding workflows still over-rely on generic tools and `bash`
- file reading and editing primitives are too coarse
- subagents are still effectively synchronous child loops that return a summary
- delegation is not yet a first-class execution model
- failure diagnosis is possible, but not structured around benchmark outcomes

This is enough for a promising local agent platform, but not enough for a reliably strong execution agent.

## Product Positioning

### Long-Term Product Direction

Jarvis should become a general-purpose desktop agent that can eventually cover:

- coding
- documents
- research and retrieval
- spreadsheets and presentations
- multimodal assets
- local automation

### Phase Direction

This phase uses coding as the first validation domain because coding tasks:

- have clearer success and failure criteria
- force the hardest execution behaviors
- surface tool, recovery, and delegation weaknesses quickly
- are easier to benchmark repeatedly with real repository state

The correct framing is:

- generalist agent runtime
- coding-first benchmark pack
- coding-first runtime optimization

The architecture must remain reusable for future domain packs.

## Scope

This design covers one focused 4-6 week productization phase with two main workstreams:

1. task-level evaluation baseline
2. runtime upgrade for tool execution and real subagent orchestration

This phase includes:

- benchmark task definitions and execution harness
- reproducible scoring and failure tagging
- coding-oriented structured tool runtime
- approval policy upgrades
- first-class subagent lifecycle and coordination primitives
- observability improvements required to debug benchmark failures

## Non-Goals

This phase explicitly does not try to finish the full general-agent roadmap.

It does not include:

- large-scale multimodal capability expansion
- broad UI redesign
- teammate productization beyond what runtime architecture requires
- new long-term memory paradigms
- multi-tenant cloud architecture
- swarm orchestration or multi-level coordinator systems
- automatic merge, cherry-pick, or cross-worktree patch integration
- large non-coding benchmark packs

## Principles

### Product Truth Must Be Task-Level

The system should be judged by real task outcomes, not only by conversation quality or internal architecture cleanliness.

### Core Runtime Must Remain Domain-Agnostic

Turns, approvals, checkpoints, subagent lifecycle, tool registry, context assembly, and observability should not be designed as coding-only concepts.

### Domain Packs Should Specialize On Top Of The Core

Coding should be the first strong domain pack, not the shape of the entire system. Future document or research packs should be able to reuse the same core runtime.

### Prefer Structured Execution Over Shell Freedom

If a high-frequency task can be represented as a structured tool, the runtime should prefer that over sending the model through raw shell flows.

### Delegation Must Be Real Or Not Claimed

If Jarvis claims to use subagents, those subagents must be durable execution units with their own lifecycle. A synchronous child loop that only returns a summary is not sufficient.

### Every Runtime Improvement Must Map To A Measured Failure Class

No large runtime refactor should land in this phase unless it clearly targets one or more benchmark failure tags.

## Success Criteria

This phase should be considered successful only if both product and platform outcomes improve.

### Product Outcomes

- coding task pass rate improves on a fixed benchmark set
- first-pass success rate improves
- approval and iteration stalls decrease
- failures become easier to classify into a small set of stable categories

### Platform Outcomes

- subagents become first-class asynchronous execution units
- coding-critical tool paths move away from generic `bash`
- task runs are reproducible enough to compare versions
- runtime improvements can be validated by benchmark deltas instead of anecdotes

## Architecture Overview

This phase should be treated as a dual-track system:

- an evaluation track that measures task outcomes
- an execution track that upgrades runtime behavior
- a feedback loop between them

The evaluation track produces:

- benchmark tasks
- reproducible runs
- scoring
- failure tags
- regression comparisons

The execution track contains:

- lead agent loop
- tool runtime
- subagent runtime
- approval and recovery behavior
- observability

The feedback loop is the important product discipline:

1. establish a baseline
2. identify dominant failure tags
3. implement runtime changes that target those tags
4. rerun the benchmark
5. keep only changes that improve measured outcomes or materially improve diagnosability

## Workstream A: Evaluation Baseline

### Purpose

The evaluation system should answer four questions:

1. can Jarvis complete the task
2. where did it fail when it could not
3. did a runtime change improve results
4. did the change introduce regressions

### Repository Layout

Recommended structure:

- `backend/evals/tasks/`
- `backend/evals/runner/`
- `backend/evals/reports/`

If a better existing path exists later, the naming can move, but the separation of task specs, runner code, and generated reports should remain.

### Task Spec Shape

Each benchmark task should define at least:

- `id`
- `name`
- `workspace_fixture` or target workspace source
- `user_prompt`
- `execution_mode`
- `approval_policy`
- `time_budget_seconds`
- `expected_checks`
- `tags`

Optional fields may include:

- seed branch or revision
- required environment flags
- run grouping such as `smoke`, `core`, or `stretch`
- reviewer notes for partial or human-reviewed checks

### Task Set Size

The first version should stay deliberately small but real.

Recommended initial size:

- `smoke`: 6-8 tasks
- `core`: 12-18 tasks
- `stretch`: 6-10 tasks

Total initial range: 24-36 tasks.

### Task Sources

Task sources should be prioritized in this order:

1. real tasks recently completed or attempted in this repository
2. active backlog items that Jarvis is expected to help with soon
3. generalized repository task templates inspired by strong coding agents

The benchmark should reflect the work Jarvis is actually expected to do, not generic public AI leaderboard prompts.

### Execution Model

Each benchmark run should:

- create an isolated session
- bind to a deterministic workspace or fixture copy
- use a fixed runtime and model configuration snapshot
- record all durable messages, timeline events, turn state changes, approvals, tool executions, and final artifacts
- capture final diff, verification results, and termination reason

Where possible, task runs should use a clean fixture worktree or disposable workspace copy so reruns remain comparable.

### Approval Policy Adapter

Benchmark runs cannot depend on a human clicking approvals manually. The evaluation harness should provide an operator policy layer with modes such as:

- `deny_all_shell`
- `allow_shell`
- `allow_shell_if_pattern_matches`
- `manual_required`

This keeps benchmark outcomes reproducible and separates agent failure from operator stall.

### Scoring Model

Scoring should have three layers:

1. hard automated checks
2. structural runtime outcome checks
3. optional human review for tasks that are not yet fully machine-judgeable

Hard automated checks may include:

- tests passing
- required files changed
- forbidden files untouched
- diff shape matching constraints
- expected artifact or output produced

Structural outcome checks should include:

- whether the run entered the wrong workspace
- whether it terminated in waiting approval
- whether it hit iteration limits
- whether it failed recovery
- whether it left the workspace in a disallowed state

The first version should support four final labels:

- `pass`
- `partial`
- `fail`
- `invalid_run`

`invalid_run` is reserved for fixture, infrastructure, or provider problems that should not count as agent quality failures.

### Failure Taxonomy

Every failing or partial task should receive one primary failure tag and optionally one secondary tag.

Recommended initial primary tags:

- `task_understanding`
- `context_miss`
- `wrong_tool_choice`
- `edit_failure`
- `verification_missing`
- `approval_stall`
- `workspace_violation`
- `subagent_coordination`
- `runtime_recovery`
- `iteration_exhausted`

This taxonomy is intentionally small. It should only expand when the current set cannot cleanly explain recurrent benchmark failures.

### Release Gates

The evaluation system should become part of product discipline, not just a sidecar report.

Recommended gates:

- `pre-merge smoke`
- `nightly core`
- `milestone full run`

Recommended tracked metrics:

- coding task pass rate
- first-pass success rate
- median turns per successful task
- approval stall rate
- iteration limit hit rate
- workspace violation rate
- recovery success rate

## Workstream B: Runtime Upgrade

The runtime upgrade in this phase has two tightly related goals:

1. increase real coding task success rate
2. reshape execution into a reusable agent runtime rather than a narrow coding-only stack

### B1. Tool Runtime

#### Objective

Move high-frequency coding workflows away from generic shell operations and toward structured execution primitives that the model can use more reliably.

#### Design Principles

- use structured tools whenever a common action can be represented cleanly
- keep `bash` or shell execution as a fallback, not the primary path
- return compact but inspectable results, with range or page controls where needed
- log every tool call in a way that supports benchmark diagnosis

#### Tool Families

The runtime should separate tools into logical families.

`Repo introspection`

- repository state
- branch information
- working tree status
- diff and commit inspection

`Code navigation`

- file discovery
- text search
- line-range reads
- symbol-context or reference lookups where practical

`Editing`

- create file
- write file
- replace text
- apply patch
- delete file only under stricter policy

`Execution and verification`

- run tests
- run lint
- run formatter
- structured command execution

`Agent orchestration`

- spawn subagent
- wait for subagent
- cancel subagent
- provide more input to a subagent

#### Phase-One Required Tool Upgrades

This phase must deliver at least:

- `search_text`
- `read_file_range`
- `apply_patch`
- `show_status`
- `show_diff`
- `run_test`
- `structured run_command`

These tools are the highest-leverage upgrades for coding task success.

#### Approval Policy

Approval should move from a shell-only model to a side-effect-tier model:

- read-only operations: default allow
- workspace writes: configurable allow
- destructive writes: strong approval
- shell execution: strong approval with benchmark policy support
- external side effects: strong approval

This keeps the approval framework reusable across future non-coding domains.

#### Output Policy

Tool output should be large enough to be useful but bounded enough to protect context quality.

Recommended behavior:

- range-based file reads instead of fixed shallow truncation
- structured diff or status summaries
- paged or capped large command output
- explicit metadata such as exit code, target, and truncation markers

### B2. Subagent Runtime

#### Objective

Turn subagents into durable execution units with their own lifecycle, rather than synchronous child loops that only return a final summary.

#### Required Capability Shift

The target model is:

- spawn a subagent
- let it run independently
- allow the lead agent to continue local work
- wait, poll, interrupt, or cancel later
- integrate results when useful

This is materially different from the current synchronous summary-only behavior.

#### Phase-One Subagent State Model

Recommended states:

- `queued`
- `running`
- `waiting_input`
- `completed`
- `failed`
- `cancelled`

Each subagent should durably track:

- parent session and parent turn when relevant
- requested role or purpose
- prompt and later follow-up inputs
- execution workspace
- isolation mode
- latest summary
- produced artifacts
- tool execution log

#### Required Subagent Operations

This phase must deliver:

- `spawn_subagent`
- `wait_subagent`
- `send_subagent_input`
- `cancel_subagent`

These operations are sufficient for a first real delegation model without introducing swarm complexity.

#### Isolation Policy

Subagents should continue to support:

- `shared`
- `worktree`

Recommended phase-one policy:

- read-only investigation: default `shared`
- likely-write implementation tasks: default `worktree`
- potentially conflicting code changes: require `worktree`
- non-git workspace isolation: fail clearly or use an explicitly chosen fallback, but never pretend isolation exists

#### Delegation Policy

The lead agent should not delegate by default for everything. Delegation should be encouraged for:

- independent investigation threads
- side work that does not block the next local step
- parallel verification or evidence-gathering tasks

Delegation should be discouraged for:

- urgent blocking work needed immediately
- trivial one- or two-tool subtasks
- tightly coupled edit loops on the same file set

This policy should be represented in both prompting and runtime behavior so delegation becomes deliberate instead of accidental.

### B3. Recovery And Observability

#### Recovery

This phase does not require token-perfect recovery, but it does require durable diagnosability and turn resumability at the phase level.

At minimum, the runtime should preserve:

- phase checkpoints
- pending tool context
- approval wait context
- interrupted subagent state
- terminal failure reason

#### Observability

Each task run should expose enough evidence to explain success or failure:

- input task
- session and turn lineage
- subagent lineage
- tool call sequence
- approvals triggered
- runtime duration
- iteration limit usage
- final diff or artifact output
- final benchmark label and failure tag

Without this evidence, benchmark failures will remain hard to fix even if they are measurable.

## 4-6 Week Delivery Plan

### Phase 0: Success Contract

Duration: approximately half a week.

Deliverables:

- fixed milestone success criteria
- benchmark scope freeze
- runtime scope freeze
- agreed metric definitions

Acceptance criteria:

- the team aligns on primary metrics
- the team aligns on what is out of scope for this phase

### Phase 1: Evaluation Foundation

Duration: week 1.

Deliverables:

- benchmark task spec format
- task runner
- approval policy adapter
- result and artifact collection
- initial 24-36 task coding benchmark pack
- machine-readable report output

Acceptance criteria:

- the same task can be rerun reproducibly
- every run emits enough evidence for diagnosis
- smoke runs can compare baseline and changed runtime builds

### Phase 2: Tool Runtime V1

Duration: weeks 2-3.

Deliverables:

- structured coding tool pack
- upgraded approval tiers
- better bounded output policies
- tool-level observability support

Acceptance criteria:

- major coding read, search, diff, and verification flows no longer depend on shell as the default path
- benchmark failure rates for tool misuse and edit failures decrease

### Phase 3: Subagent Runtime V1

Duration: weeks 3-4.

Deliverables:

- durable subagent lifecycle
- spawn, wait, send-input, and cancel operations
- worktree and shared isolation rules
- lead-agent delegation policy
- subagent execution evidence collection

Acceptance criteria:

- the lead agent can launch a subagent, continue local work, and later integrate results
- benchmark tasks that benefit from parallel investigation improve measurably

### Phase 4: Stabilization And Gates

Duration: weeks 5-6.

Deliverables:

- pre-merge smoke gate
- nightly core benchmark
- milestone full-run report
- top failure-tag review process
- targeted fixes for approval, recovery, and iteration-limit regressions

Acceptance criteria:

- improvements are stable across repeated runs
- regressions are visible quickly
- most failures land in a small, interpretable failure taxonomy

## Staffing Model

Recommended workstream ownership:

- `eval lane`
- `runtime lane`

The eval lane owns task packs, runner behavior, scoring, reports, and failure taxonomy.

The runtime lane owns tool runtime, subagent runtime, approval behavior, recovery behavior, and observability improvements.

Cross-lane rules:

- every major runtime change should cite a target benchmark failure class
- every benchmark review should report not only totals but also failure distribution
- weekly review should focus on which failure tags dropped and which new ones rose

## Risks And Controls

### Scope Expansion

Risk: the phase expands into too many domains or too many product surfaces.

Control: keep the benchmark pack coding-first and defer broader domain packs.

### Tool Count Grows Faster Than Success Rate

Risk: many tools are added without improving outcomes.

Control: require each tool upgrade to map to benchmark failure hypotheses.

### Subagent Architecture Becomes Complex Without Product Gain

Risk: orchestration becomes elaborate but does not improve tasks.

Control: only support a small number of clear delegation scenarios in phase one.

### Evaluation Maintenance Cost Becomes Too High

Risk: the benchmark becomes expensive to maintain before it becomes useful.

Control: keep v1 small, hard, and realistic rather than broad.

## Acceptance Criteria

This phase is complete only when all of the following are true:

- Jarvis has a working task-level benchmark harness for coding tasks
- benchmark runs can classify outcomes into pass, partial, fail, or invalid
- benchmark failures can be tagged with a compact primary taxonomy
- coding-critical execution paths rely primarily on structured tools instead of generic shell fallback
- subagents support durable spawn, wait, send-input, and cancel flows
- the runtime core remains reusable for future non-coding domain packs
- milestone reports show measurable improvement in coding-task outcomes over the initial baseline

## Final Outcome

At the end of this phase, Jarvis should not yet be a fully realized generalist agent across every domain.

It should instead be:

- a generalist agent runtime with cleaner architectural boundaries
- a coding-first benchmarked system with measured product truth
- a platform that has already survived one hard validation domain
- a base that can later add document, research, and multimodal domain packs without discarding the evaluation or runtime core built here

That is the correct starting point for a full-capability agent product.
