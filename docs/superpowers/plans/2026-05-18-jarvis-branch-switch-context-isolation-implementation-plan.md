# Jarvis Branch Switch Context Isolation Implementation Plan

Date: 2026-05-18
Depends on: `docs/superpowers/specs/2026-05-18-jarvis-branch-switch-context-isolation-design.md`

## Delivery Strategy

Implement in-session branch switching as a Git-backed session rebind in four phases:

1. Branch context persistence and safety checks
2. Git switch runtime operations
3. Composer branch picker UI
4. Context isolation, recovery, and verification

The plan intentionally excludes automatic stash, auto-merge, and auto-commit behavior. The first release should make branch switching safe and explicit, not aggressively automated.

## Phase 1: Branch Context Persistence And Safety Checks

### Objectives

- Add branch-scoped context identity to the session model
- Block unsafe branch switches before any Git command runs

### Tasks

- Add `branch_context_id` to:
  - `sessions`
  - `turns`
  - `approvals`
  - `session_memory`
- Initialize `branch_context_id` during session creation for Git-backed sessions
- Add a backend safety-check path that verifies:
  - `git_enabled=true`
  - working tree is clean
  - no running turn exists in the active branch context
  - no waiting approval exists in the active branch context
  - no resumable interrupted turn exists in the active branch context
- Return structured failure reasons for blocked switches

### Exit Criteria

- Git-backed sessions persist `branch_context_id`
- branch switch preconditions are enforced in backend code
- failure reasons are machine-readable enough for UI messaging

## Phase 2: Git Switch Runtime Operations

### Objectives

- Add explicit switch-to-branch and create-and-switch operations
- Refresh lead Git metadata atomically after successful switch

### Tasks

- Extend Git service with:
  - list local branches
  - validate new branch names
  - switch to existing branch
  - create and switch to new branch
- Add runtime endpoints or actions for:
  - list branches
  - switch existing branch
  - create and switch new branch
- On successful switch:
  - refresh `lead_branch`
  - refresh `head_revision`
  - refresh `working_tree_status`
  - generate a new `branch_context_id`
  - emit `session.branch_switched`
- Ensure failed Git operations do not mutate persisted session state

### Exit Criteria

- existing-branch switching works
- create-and-checkout new branch works
- session Git metadata refreshes after success
- failed switch attempts leave session state unchanged

## Phase 3: Composer Branch Picker UI

### Objectives

- Add a branch control under the composer
- Let the user search, select, and create branches without leaving the conversation surface

### Tasks

- Add a branch button under the composer for Git-backed sessions
- Display the current lead branch in the button
- Build a branch picker card with:
  - search field
  - branch list
  - current-branch highlight
  - create-and-checkout action
- Call the new backend APIs for branch list, branch switch, and branch creation
- Surface blocked-switch reasons in the UI when preconditions fail
- Hide the control entirely for non-Git sessions

### Exit Criteria

- Git-backed sessions show the branch picker control
- the picker can search and select existing branches
- the picker can create and checkout a new branch
- blocked switch attempts show clear messages

## Phase 4: Context Isolation, Recovery, And Verification

### Objectives

- Ensure new-branch work does not reuse old-branch context
- Keep recovery, approvals, and memory aligned with the active branch context

### Tasks

- Scope turn queries by active `branch_context_id`
- Scope approval queries and recovery lookups by active `branch_context_id`
- Scope session memory retrieval and writes by active `branch_context_id`
- Emit branch-switch timeline events with source and target branch metadata
- Add or update tests for:
  - dirty working tree rejection
  - running turn rejection
  - waiting approval rejection
  - resumable interrupted turn rejection
  - branch-context isolation for turns, approvals, and memory
  - branch picker rendering and search behavior

### Exit Criteria

- switching branches creates a new isolated branch context
- old-branch turns, approvals, and memory do not automatically participate in the new branch
- recovery behavior remains consistent after switching

## Suggested Implementation Order

1. Add `branch_context_id` and branch-switch safety checks
2. Add backend Git branch listing and switching operations
3. Add composer branch picker UI and branch-create flow
4. Scope turns, approvals, and memory by `branch_context_id`
5. Add verification coverage and polish event messaging

## Files And Areas Expected To Change

- `backend/app/models/entities.py`
- `backend/app/db/session.py`
- `backend/app/services/git_service.py`
- `backend/app/services/session_service.py`
- `backend/app/services/turn_service.py`
- `backend/app/services/approval_service.py`
- `backend/app/services/memory_*`
- `backend/app/runtime/manager.py`
- `backend/app/api/routes.py`
- `frontend/src/app/App.tsx`
- `frontend/src/app/styles.css`
- `frontend/src/lib/api.ts`
- `backend/tests/`

## Risks To Manage

- leaking old-branch memory into the new branch context
- switching branch state in Git but failing to refresh session metadata consistently
- allowing UI actions that the backend later rejects without clear explanation
- making branch switching appear safe while hidden resumable work still exists

## Definition Of Done

Branch switching is done when a Git-backed session can safely list branches, search branches, switch to an existing branch, create and checkout a new branch, block unsafe switches, generate a new `branch_context_id` on success, and prevent turns, approvals, and memory from the previous branch context from leaking into the new one.
