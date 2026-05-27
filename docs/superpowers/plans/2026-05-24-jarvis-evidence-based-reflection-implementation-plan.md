# Jarvis Evidence-Based Reflection Implementation Plan

Date: 2026-05-24
Depends on: `docs/superpowers/specs/2026-05-24-jarvis-evidence-based-reflection-design.md`

## Delivery Strategy

Implement evidence-based completion control in three phases:

1. false-completion guardrails for high-risk runtime paths
2. reusable verifier architecture and packet assembly
3. broader domain coverage, eval integration, and observability polish

The plan intentionally starts with narrow high-risk fixes instead of a full verifier rewrite. The first milestone is to stop obvious false completions. The second is to unify those protections behind a reusable acceptance layer.

## Phase 1: False-Completion Guardrails

### Objectives

- remove the most damaging false-positive completion paths quickly
- stop treating weak or wrong-context evidence as successful verification
- introduce `repair` as a first-class runtime outcome

### Tasks

- Tighten `run_test` semantics in the structured tool broker:
  - reject obviously mutating package-management commands such as `pip install`, `npm install`, `poetry add`, and similar side-effecting environment mutations
  - classify supported command shapes into:
    - verification
    - read-only inspection
    - blocked mutation
- Add target-environment awareness for Python verification:
  - detect whether the workspace has `.venv/bin/python`
  - when the task is environment-sensitive, prefer the target environment interpreter over system `python3`
  - record which interpreter actually executed the command
- Replace coarse "tool was called" verification checks in runtime reflection:
  - stop treating any `run_test` call as sufficient verification
  - require command-aware verification evidence for high-risk task kinds
- Introduce a minimal verification-scope resolver:
  - classify turns into `none | soft | hard`
  - only run hard completion gating for tasks that make failure-bearing completion claims
- Extend `ReflectionDecision` or adjacent runtime verdict handling with `repair`
- Add high-risk obligation checks for first-stage coverage:
  - code changes
  - dependency installation
  - environment setup
  - time-sensitive external facts

### Exit Criteria

- `requirements.txt` edits no longer count as dependency installation
- system-interpreter success no longer counts as project-environment success
- non-verification `run_test` commands do not satisfy verification requirements
- the runtime can emit `repair` when evidence contradicts the claimed result

## Phase 2: Reusable Verifier Architecture

### Objectives

- replace scattered completion heuristics with a structured acceptance layer
- make obligations, claims, and evidence explicit runtime objects
- preserve current lead-agent execution shape while moving completion judgment into dedicated services

### Tasks

- Add `task_profile_service`:
  - infer task kinds
  - infer verification level
  - assign risk level
  - seed initial obligations for supported task types
- Add `verification_packet_service`:
  - gather task profile, obligations, claims, and evidence into one verifier input object
  - extract final completion claims from the assistant response
  - map claims back to obligations
- Add `evidence_verifier`:
  - score evidence per obligation
  - emit per-obligation verdicts:
    - `satisfied`
    - `missing_evidence`
    - `conflicting_evidence`
    - `blocked`
  - emit turn verdicts:
    - `done`
    - `continue`
    - `repair`
    - `blocked`
    - optional `uncertain`
- Add `repair_planner`:
  - convert verifier output into targeted repair actions or continuation prompts
  - distinguish "gather more evidence" from "fix the broken result"
- Update runtime orchestration:
  - keep `_run_reflection()` as the completion-boundary entry point
  - move actual judgment into profiler, packet, verifier, and repair services
  - persist verifier results alongside checkpoints and reflection records

### Exit Criteria

- the runtime can assemble a verification packet for supported task types
- `done` is driven by per-obligation evidence rather than by tool-name heuristics
- repair prompts are generated from missing or conflicting obligations instead of from generic follow-up text

## Phase 3: Domain Expansion, Eval Integration, And Observability

### Objectives

- extend the verifier beyond the first narrow task set
- connect verifier output to eval and product metrics
- improve diagnosability of completion failures

### Tasks

- Expand obligation and assertion coverage to additional task domains:
  - document creation and export
  - automation side effects
  - richer external-fact tasks
- Extend eval runner evidence capture to include:
  - turn verdict
  - obligation verdicts
  - repair count
  - false-done failure tags
- Add regression tasks and trace replay for known bad cases
- Improve timeline or durable records so completion outcomes are queryable without manual checkpoint inspection
- Add optional domain-specific uncertainty policies for tasks that legitimately permit `uncertain`

### Exit Criteria

- verifier outcomes appear in eval and runtime observability surfaces
- replaying known bad traces reliably reproduces non-`done` outcomes
- more than one task domain can use the same verifier architecture

## Suggested Implementation Order

1. Restrict `run_test` and separate verification from mutation commands
2. Add minimal verification-scope resolution and `repair`
3. Add target-environment-aware Python verification
4. Introduce `task_profile_service`
5. Introduce `verification_packet_service`
6. Introduce `evidence_verifier` and `repair_planner`
7. Wire verifier output into evals, timeline records, and trace replay
8. Expand obligation coverage to more domains

## Files And Areas Expected To Change

- `backend/app/runtime/manager.py`
- `backend/app/tools/broker.py`
- `backend/app/mcp/registry.py`
- `backend/app/services/reflection_service.py`
- `backend/app/services/checkpoint_service.py`
- `backend/app/services/`
  - `task_profile_service.py`
  - `verification_packet_service.py`
  - `evidence_verifier.py`
  - `repair_planner.py`
- `backend/app/models/entities.py`
- `backend/app/models/__init__.py`
- `backend/app/db/session.py`
- `backend/evals/`
- `backend/tests/`

## Risks To Manage

- over-verifying low-risk informational turns and degrading responsiveness
- under-modeling obligations so the verifier still misses implicit completion claims
- classifying legitimate verification commands as blocked mutations
- tying verifier logic too closely to coding tasks and losing generality
- introducing new runtime states without making recovery and observability coherent

## Definition Of Done

Evidence-based reflection is done for the first milestone when Jarvis can detect when a turn is making failure-bearing completion claims, run hard verification only for those turns, reject weak or wrong-context evidence for critical obligations, emit `repair` or `blocked` instead of false `done` outcomes, and replay known false-completion bad cases without regressing back to language-only acceptance.
