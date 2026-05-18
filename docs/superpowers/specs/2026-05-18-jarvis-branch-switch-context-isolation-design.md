# Jarvis Branch Switch Context Isolation Design

## Goal

Add an in-session branch switch capability for Git-backed workspaces, while guaranteeing that switching branches does not carry over short-term or long-term working context from the previous branch.

The user should be able to select an existing branch or create and checkout a new branch from the composer area. After a successful switch, the session continues in the same workspace but on a new branch-scoped context.

## Problem

Jarvis now understands lead Git repository and branch state for a session, and subagents can already run in isolated worktrees and branches. However, the product still lacks a safe model for switching the lead branch inside the current session.

Without explicit context isolation, branch switching would be unsafe for three reasons:

- short-term working context such as active turns, checkpoints, and approvals may still reflect the previous branch
- long-term session memory may carry branch-specific decisions and constraints into the new branch
- the UI may show a new Git branch while the runtime still reasons from the old branch's state

This means branch switching cannot be treated as a simple UI control or a plain `git switch` wrapper. It must be a deliberate session context rebind.

## Scope

This spec covers the first branch-switching implementation for Git-backed sessions.

It defines:

- the user interaction model for selecting and creating branches
- preconditions required before a lead-branch switch can execute
- the branch-scoped context model needed to prevent memory bleed
- the runtime and persistence changes required to support safe in-session branch switching

## Non-Goals

- automatically stashing, restoring, or rebasing work during branch switching
- allowing branch switching when the working tree is dirty
- allowing branch switching while a lead turn is running or waiting for approval
- preserving old-branch session memory inside the new branch context
- automatically merging subagent branches into the new lead branch after switching

## Approaches Considered

### Approach A: Switch Branch In Place Without Context Isolation

The product runs `git switch`, updates the header, and leaves the rest of the session untouched.

This is not acceptable. It would let the agent continue with stale branch-specific memory, stale recovery state, and stale approvals.

### Approach B: Switch Branch And Create A New Session

Every branch change opens a fresh session and abandons the old one.

This is the safest model, but it does not match the desired interaction. The user explicitly wants branch switching to happen inside the current session.

### Approach C: Switch Branch Inside The Current Session With Branch Context Rebind

The session remains the same top-level object, but a successful branch switch generates a new branch-scoped context identity. Subsequent turns, approvals, checkpoints, and memory use that new context only.

This is the recommended approach.

## Product Decision

Choose Approach C.

Branch switching should happen in the current session, but it must create a new branch-scoped execution and memory context.

## Core Principles

### Branch Switching Is A Rebind, Not A Cosmetic Toggle

A successful branch switch changes the semantic execution context of the session. It is not equivalent to changing a filter or UI view.

### Branch Context Must Be Isolated

Turns, approvals, checkpoints, and memory from the previous branch must not silently participate in the new branch's reasoning path.

### Switching Requires A Safe Workspace State

The first implementation should refuse to switch branches unless the working tree is clean and the session has no active or resumable execution state.

### Failure Must Be Atomic

If branch switching fails at any stage, the session must remain on the original branch and retain its original branch context.

## User Experience

### Branch Button

When the current session is Git-backed, the composer area should show a branch button below the input.

The button should display:

- a branch icon
- the current lead branch name
- a disclosure affordance

If the session is not Git-backed, the button should not render.

### Branch Picker Card

Clicking the branch button opens a compact card anchored below it.

The card should include:

- a search field for filtering branches
- a scrollable list of available local branches
- current branch highlighting
- an action row for `Create and checkout new branch...`

The first implementation only needs local branches. Remote branch tracking can remain a later enhancement.

### Selecting An Existing Branch

Clicking a listed branch attempts a switch only if all preconditions pass.

If the selected branch is already the current lead branch, the card closes without side effects.

### Creating A New Branch

The card should allow entry of a new branch name. Submitting it attempts:

- validate name locally
- `git switch -c <branch>`

If successful, the session moves into a new branch context on that new branch.

## Preconditions

Branch switching must be blocked unless all of these are true:

- `git_enabled=true`
- working tree status is `clean`
- there is no active `running` turn
- there is no `waiting_approval` turn
- there is no interrupted turn with resumable checkpoint state
- the target branch differs from the current lead branch

If any precondition fails, the UI should explain why. The runtime should enforce the same checks rather than relying on the frontend alone.

## Branch Context Model

The session needs a branch-scoped context identity separate from the displayed Git branch name.

Recommended new field:

- `branch_context_id`

This field is required because:

- a branch name is not a stable context identity across repeated switches
- detached HEAD has no durable branch name
- future explicit context reset behavior is easier to model with an opaque branch-context identity

### Session Fields

Add to `sessions`:

- `branch_context_id`

The session still keeps:

- `lead_branch`
- `repo_root`
- `head_revision`
- `working_tree_status`
- `detached_head`

### Branch-Scoped Entities

Add `branch_context_id` to:

- `turns`
- `approvals`
- `session_memory`

This makes the current branch context the default filter for:

- active and historical turns shown as relevant to the current branch
- resumable checkpoints and interrupted-turn recovery
- pending approvals
- memory retrieval and memory writes

Old branch data remains durable, but does not automatically participate in the new branch.

## Branch Switch Execution Model

### Existing Branch

Switch flow:

1. Validate preconditions
2. Run `git switch <target-branch>`
3. Refresh repository metadata
4. Generate a new `branch_context_id`
5. Update session Git fields and `branch_context_id`
6. Emit a `session.branch_switched` event

### New Branch

Switch flow:

1. Validate preconditions
2. Validate the requested branch name
3. Run `git switch -c <new-branch>`
4. Refresh repository metadata
5. Generate a new `branch_context_id`
6. Update session Git fields and `branch_context_id`
7. Emit a `session.branch_switched` event

In both cases, the newly active branch context is empty of prior branch-scoped working memory until new work begins.

## Recovery And Isolation Rules

### Active Turn And Approval Safety

The runtime must reject branch switching when the current branch context has:

- a running turn
- a waiting approval turn
- a resumable interrupted turn

This avoids switching away from execution that still semantically belongs to the old branch.

### Post-Switch Memory Rules

After a successful switch:

- new turns are created with the new `branch_context_id`
- new memory writes use the new `branch_context_id`
- memory retrieval only reads from the active `branch_context_id`
- approval lookup and turn recovery only consider the active `branch_context_id`

This is the main protection against cross-branch memory contamination.

### Timeline History

Timeline history may remain visible at the session level, but the runtime should emit an explicit branch-switch event so the history has a visible separation point.

Recommended event:

- `session.branch_switched`

Suggested content:

- source branch
- target branch
- whether the switch was to an existing branch or a newly created branch

## Failure Handling

### Validation Failure

If preconditions fail, the runtime returns a structured error and performs no repository mutation.

Examples:

- working tree dirty
- running turn exists
- waiting approval exists
- resumable interrupted turn exists
- target branch equals current branch

### Git Command Failure

If `git switch` or `git switch -c` fails:

- session Git metadata stays unchanged
- `branch_context_id` stays unchanged
- no branch-switch event is emitted

### Partial State Update Failure

If Git succeeds but database update fails, the runtime should report failure and require operator inspection. The implementation should order operations to make this extremely unlikely, but the design must still acknowledge the possibility.

## Testing Requirements

- Git-backed session branch list and branch search tests
- existing-branch switch tests
- create-and-checkout new branch tests
- dirty working tree rejection tests
- running turn rejection tests
- waiting approval rejection tests
- resumable interrupted turn rejection tests
- branch-context scoping tests for:
  - turns
  - approvals
  - session memory
- UI tests for branch picker rendering and current-branch selection state

## Acceptance Criteria

- Git-backed sessions show a branch button below the composer
- the branch picker can search existing local branches
- the picker can create and checkout a new branch
- branch switching is blocked if the working tree is dirty
- branch switching is blocked if the session has running, waiting-approval, or resumable interrupted work
- a successful switch updates lead branch metadata and generates a new `branch_context_id`
- turns, approvals, and memory from the old branch do not automatically carry into the new branch context
- the session emits a visible branch-switch event when switching succeeds
