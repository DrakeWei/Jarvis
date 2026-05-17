# Jarvis Subagent Worktree Isolation Design

## Goal

Add true Git worktree isolation for Jarvis subagents so a subagent can execute in an independent filesystem and branch without mutating the parent session workspace directly.

## Problem

Jarvis currently supports subagents as bounded child agent loops, but they execute in the same session workspace as the lead agent. That causes write conflicts, mixes partial experimental changes into the parent workspace, and leaves no durable isolated branch state to inspect after completion.

The current session invariant is still correct: one session binds to one canonical workspace. The missing piece is file-level isolation for child execution.

## Scope

This spec covers the first implementation of subagent worktree isolation.

It defines:

- isolation modes for subagent runs
- required persistence changes
- worktree lifecycle and cleanup rules
- user-visible API and timeline behavior
- phase-one constraints that preserve the existing session workspace model

## Non-Goals

- coordinator mode or swarm execution
- automatic merge, cherry-pick, or patch application
- lead-session worktree switching
- long-term memory scoped to temporary worktrees
- fake isolation for non-git directories

## Principles

### Session Workspace Invariant

The session keeps exactly one canonical workspace. A subagent worktree does not rebind the session and does not become the new default workspace.

### Real Isolation Or Explicit Failure

If `worktree` isolation is requested, Jarvis must either execute in a real Git worktree or fail clearly. It must not silently fall back to shared execution.

### Preserve Evidence

If a subagent changes files or cleanup fails, Jarvis should preserve the worktree and expose its path and branch instead of hiding the state.

### Reuse Existing Execution Plumbing

The current agent loop and `ToolBroker` already accept a concrete workspace path. The isolation design should change the workspace source for a subagent, not duplicate the loop or tool stack.

### No Temporary Worktree Memory

Phase one keeps durable memory scoped to the session canonical workspace only. A worktree subagent returns a summary, but does not write long-term memory facts.

## Isolation Modes

- `shared`: current behavior, execute in the session workspace
- `worktree`: create a Git worktree and execute there

`shared` remains the default for backward compatibility. `worktree` is explicit.

## Architecture

Add `backend/app/services/worktree_service.py`.

`worktree_service` owns:

- git repo detection for the base workspace
- repo root and base revision discovery
- branch and path naming
- `git worktree add` creation
- dirty-state inspection after execution
- cleanup or preservation of the worktree

`RuntimeManager` remains the orchestration entry point. It decides which isolation mode to use, asks `worktree_service` for an execution workspace when needed, then calls the existing agent loop with that workspace.

## Data Model

Extend `AgentRecord` with:

- `base_workspace_path`
- `execution_workspace_path`
- `isolation_mode`
- `git_branch`
- `git_base_revision`
- `cleanup_status`
- `preserved_reason`

Recommended values:

- `isolation_mode`: `shared` or `worktree`
- `cleanup_status`: `pending`, `cleaned`, `preserved`, `cleanup_failed`
- `preserved_reason`: `dirty_worktree`, `cleanup_error`, `run_failed`, `workspace_not_git_repo`, `worktree_create_failed`

Extend subagent schemas so the create payload accepts `isolation_mode` and the returned summary includes the new runtime state fields.

## Lifecycle

### Shared Mode

1. Resolve the session workspace.
2. Run the existing subagent loop in that workspace.
3. Persist `isolation_mode=shared`.

### Worktree Mode

1. Resolve the session workspace.
2. Verify the workspace belongs to a Git repository.
3. Resolve repository root and current base revision.
4. Create a worktree path under `<repo-root>/.jarvis/worktrees/<agent-id>-<slug>/`.
5. Create a branch dedicated to the subagent run.
6. Run the existing subagent loop with `execution_workspace_path` set to the worktree directory.
7. Inspect worktree dirtiness after completion.
8. Remove the worktree if it is clean.
9. Preserve the worktree if it contains changes or cleanup fails.

The worktree path should be deterministic enough to inspect later, but unique enough to avoid collisions. The branch name should include the agent id and a short slug derived from the subagent name.

## Failure Handling

Errors should be explicit and durable:

- `workspace_not_git_repo`: fail the subagent run before execution
- `worktree_create_failed`: fail before execution and record the reason
- `subagent_run_failed`: mark the run failed and preserve the worktree for inspection
- `worktree_cleanup_failed`: return the subagent result but record cleanup failure and preserve the worktree

The runtime must not silently downgrade `worktree` to `shared`. Failure to isolate is failure of the requested run mode.

## User-Visible Results

The first implementation should expose the minimum information needed for safe follow-up:

- timeline start events show the selected isolation mode
- timeline summary events include branch and preserved path when relevant
- subagent summaries expose `shared` versus `worktree`
- preserved worktrees remain inspectable through their absolute path

The first implementation should not add merge UI. If a worktree is preserved, the lead agent or user may choose the next action in a later turn.

## API And Runtime Changes

`SubagentRunCreate` should accept `isolation_mode`, defaulting to `shared`.

`RuntimeManager.run_subagent()` should:

1. create the `AgentRecord`
2. resolve isolation mode
3. create a worktree execution context when requested
4. run the existing subagent loop in the returned workspace
5. persist cleanup outcome and final summary

The current `ToolBroker` path-scoping model can remain unchanged. The only difference is the workspace path passed to it.

## Memory And Context Rules

Phase one keeps session memory rules unchanged:

- the session canonical workspace remains the source of durable memory identity
- worktree subagents may read and write inside their temporary worktree
- worktree subagents return summary text to the parent session
- worktree subagents do not write new long-term memory facts tied to the temporary workspace

This avoids polluting session memory with temporary branch-specific state before the system has explicit multi-workspace memory semantics.

## Testing Requirements

- unit tests for git repo detection and non-git rejection
- unit tests for worktree path and branch naming
- unit tests for cleanup decisions on clean versus dirty worktrees
- runtime tests confirming `shared` and `worktree` modes choose different execution roots
- failure tests for create failure, run failure, and cleanup failure
- API tests confirming new fields are returned in subagent summaries

## Rollout Plan

### Phase 1

Implement subagent worktree isolation only.

### Phase 2

Improve operator visibility with richer timeline text and UI badges.

### Phase 3

Use the same isolation service for background workers or future coordinator-driven worker execution.

## Acceptance Criteria

- a subagent can be launched in `worktree` mode from a Git-backed session workspace
- the subagent runs in a different execution directory than the session canonical workspace
- clean worktrees are removed automatically
- dirty or failed worktrees are preserved and reported back
- isolation metadata is queryable from the existing subagent APIs
- no session rebind occurs during worktree execution
- no long-term session memory is written against the temporary worktree
