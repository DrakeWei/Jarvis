# Jarvis Main Agent Git-Aware Implementation Plan

Date: 2026-05-17
Depends on: `docs/superpowers/specs/2026-05-17-jarvis-main-agent-git-aware-design.md`

## Delivery Strategy

Implement lead-agent Git awareness in four phases:

1. Repository detection and persistence
2. Runtime branch awareness
3. UI and session-state visibility
4. Verification and subagent-linkage hardening

The plan intentionally stops short of automatic stage, commit, or merge behavior. Phase one is about making Git state explicit and reliable, not about turning the lead agent into a fully automated Git operator.

## Phase 1: Repository Detection And Persistence

### Objectives

- Detect whether a selected workspace belongs to a Git repository
- Persist lead repository and branch metadata on the session

### Tasks

- Add a dedicated Git inspection service for:
  - repository root detection
  - branch detection
  - HEAD revision lookup
  - dirty state detection
  - detached HEAD detection
- Extend session persistence with Git-aware fields:
  - `repo_root`
  - `git_enabled`
  - `lead_branch`
  - `head_revision`
  - `working_tree_status`
  - `detached_head`
- Populate these fields during session creation
- Refresh these fields during explicit workspace rebind or equivalent session-reopen refresh path

### Exit Criteria

- Git-backed workspaces persist lead repository and branch metadata
- Non-Git workspaces remain valid and store `git_enabled=false`
- Detached and dirty states are distinguishable in persisted session state

## Phase 2: Runtime Branch Awareness

### Objectives

- Make the lead runtime aware of repository and branch context
- Connect subagent branch isolation back to the lead branch model

### Tasks

- Inject lead Git metadata into the lead-agent system prompt or runtime context pack
- Ensure lead-agent responses can answer branch-aware questions correctly
- Update session-state assembly and timeline helpers to expose Git state where needed
- Ensure subagent worktree creation records or derives its parent lead branch target from the current session
- Keep ordinary lead-agent file edits on the current working tree path without introducing automatic Git mutations

### Exit Criteria

- The lead agent knows which branch it is operating on when `git_enabled=true`
- Subagent isolated branch work is conceptually tied to the lead session branch
- No automatic stage, commit, or merge behavior is introduced

## Phase 3: UI And Session-State Visibility

### Objectives

- Surface Git state clearly to the user without making the UI heavy
- Preserve current session behavior for non-Git workspaces

### Tasks

- Add compact lead Git state to the session header:
  - branch
  - clean or dirty status
  - detached HEAD indicator when relevant
- Ensure the UI degrades cleanly when `git_enabled=false`
- Preserve current subagent branch and worktree presentation, while clarifying that subagent branches are subordinate to the lead session branch
- Surface branch mismatch or refresh state if the repository changes outside Jarvis between opens

### Exit Criteria

- Git-backed sessions visibly show lead branch state
- Non-Git sessions remain visually clean and unchanged
- Subagent branch state is legible and clearly related to the lead context

## Phase 4: Verification And Subagent-Linkage Hardening

### Objectives

- Verify detection, persistence, and runtime usage of lead Git state
- Ensure the new metadata does not break current session or subagent behavior

### Tasks

- Add unit tests for Git detection service:
  - Git workspace
  - non-Git workspace
  - dirty working tree
  - detached HEAD
- Add session creation tests proving Git metadata is persisted correctly
- Add runtime context tests proving lead Git metadata reaches the lead agent
- Add subagent tests proving isolated branches still map back to a lead session with Git metadata
- Run frontend verification for header rendering and session-state display

### Exit Criteria

- Git detection and persistence are covered by tests
- Existing subagent worktree behavior still passes verification
- UI renders Git-backed and non-Git sessions without regression

## Suggested Implementation Order

1. Add Git inspection service and session-level Git fields
2. Populate Git metadata during session creation and refresh flows
3. Inject Git state into lead-agent runtime context
4. Render Git state in the session header
5. Harden subagent linkage and add tests

## Files And Areas Expected To Change

- `backend/app/core/` or `backend/app/services/` for Git inspection helpers
- `backend/app/models/entities.py`
- `backend/app/db/session.py`
- `backend/app/services/session_service.py`
- `backend/app/runtime/manager.py`
- `backend/app/schemas/events.py` or session summary schemas as needed
- `frontend/src/app/App.tsx`
- `frontend/src/app/styles.css`
- `frontend/src/lib/api.ts`
- `backend/tests/`

## Risks To Manage

- Treating current checkout state as permanently authoritative when the repository may change outside Jarvis
- Making Git state visible but not clear enough for detached or dirty conditions
- Accidentally implying automatic merge or commit behavior before those flows exist
- Coupling subagent branch metadata too tightly to speculative future merge implementations

## Definition Of Done

Lead-agent Git awareness is done when Jarvis can reliably detect Git-backed workspaces, persist and display lead repository and branch state, inject that state into lead-agent runtime context, keep non-Git workspaces working unchanged, and continue to run subagent worktree isolation as a subordinate branch workflow without introducing automatic merge or auto-commit behavior.
