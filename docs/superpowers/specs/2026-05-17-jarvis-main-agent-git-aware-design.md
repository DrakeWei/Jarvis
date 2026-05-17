# Jarvis Main Agent Git-Aware Design

## Goal

Make the lead Jarvis agent Git-aware when the selected workspace is inside a Git repository, while keeping ordinary file execution semantics intact for non-Git workspaces and for early Git-enabled phases.

The lead agent should know which repository and branch it is operating on. Subagents should continue to use isolated branches and worktrees. Results should flow back toward the lead agent branch through explicit user-directed actions rather than automatic merge.

## Problem

Jarvis currently treats the session workspace as a filesystem root, not a version-control root. The runtime can read and modify files inside the workspace, but it does not treat the current Git branch as first-class session state.

This creates three gaps:

- the lead agent does not know which branch it is supposed to be working against
- subagent worktree results do not have a well-defined parent branch target beyond the filesystem workspace
- the product lacks a safe, explicit model for moving isolated subagent work back toward the lead agent branch

The new subagent worktree isolation feature solved file-level isolation for delegated work, but the lead session still lacks branch identity and repository awareness.

## Scope

This spec covers the first Git-aware main-agent implementation.

It defines:

- how Jarvis detects whether a selected workspace belongs to a Git repository
- how a session binds to a lead repository and branch context
- how subagent branch and worktree runs relate back to that lead branch
- which Git metadata is visible in session and subagent state
- what explicit merge-back flow should look like in later phases

## Non-Goals

- automatically staging, committing, rebasing, or merging all lead-agent file edits
- automatically cherry-picking or merging subagent branches back into the lead branch
- turning non-Git workspaces into fake repository sessions
- allowing silent branch switching inside one active session
- redefining long-term session memory to be multi-branch aware in phase one

## Approaches Considered

### Approach A: Keep Lead Agent Filesystem-Only

The lead agent remains workspace-aware but branch-blind. Only subagents use Git concepts.

This is the current state. It keeps the runtime simple, but it leaves the product with an incomplete collaboration model now that subagents already use branch and worktree isolation.

### Approach B: Lead Agent Git-Aware, Not Fully Git-Managed

The lead session binds to repository identity and one lead branch. The runtime and UI surface repository and branch metadata, but ordinary lead-agent edits still modify the working tree directly unless a later explicit Git action is requested.

This is the recommended approach. It provides the branch context needed for safe coordination without over-automating repository operations.

### Approach C: Lead Agent Fully Git-Managed By Default

All lead-agent file edits automatically become Git-managed operations, with automatic stage, commit, and merge semantics.

This is too aggressive for phase one. It adds substantial risk around user expectations, dirty trees, implicit commits, and conflict handling before the product has the surrounding UX and approval model to support it safely.

## Product Decision

Choose Approach B.

The lead agent becomes Git-aware, not fully Git-managed.

## Core Principles

### Git Awareness Is Conditional

If the chosen workspace is not inside a Git repository, Jarvis behaves exactly like a normal filesystem-bound session. No fake Git metadata is invented.

### One Session Binds To One Workspace And One Lead Branch

The existing canonical workspace invariant remains true. When Git is available, the session also binds to one lead branch context. The session should not silently drift between branches midstream.

### Lead Agent And Subagents Have Different Git Responsibilities

The lead agent owns repository and branch context. Subagents own isolated temporary branches and worktrees derived from that lead context.

### Merge Back Is Explicit

Subagent work may be prepared for merge-back, but phase one should not automatically merge it into the lead branch.

### Dirty State Must Be Visible

Branch identity without working tree status is insufficient. The product should surface whether the lead workspace is clean, dirty, or detached before pretending it is a safe merge target.

## Repository Detection

When a workspace is selected or rebound, Jarvis should attempt Git detection with real Git commands:

- repository root: `git rev-parse --show-toplevel`
- symbolic branch: `git symbolic-ref --short HEAD`
- fallback branch display: `git branch --show-current`
- working tree status: `git status --porcelain`

Derived outcomes:

- `not_git_repo`
- `git_repo_clean`
- `git_repo_dirty`
- `git_repo_detached_head`

If repository detection fails entirely, Jarvis must stay in normal non-Git workspace mode.

## Lead Session Model

When Git detection succeeds, the session should persist:

- `repo_root`
- `git_enabled`
- `lead_branch`
- `head_revision`
- `working_tree_status`
- optional `detached_head` boolean

These fields belong to the session because they describe the lead execution context, not a transient tool call.

The session still binds to one canonical workspace path. Git metadata adds branch identity on top of that workspace binding.

## Branch Binding Rules

### Session Creation

If the chosen workspace is in a Git repository, the new session binds to the currently checked-out branch.

If the repository is in detached HEAD state, the session may still open, but the UI should surface that state clearly and avoid implying a named branch target.

### Session Reopen

On restore or reopen, Jarvis should compare persisted branch metadata with the current repository state and refresh it if the repository moved since the last session activity.

The runtime should not silently change the session's semantic lead branch if that would alter the meaning of prior work. If the current checkout differs from the persisted lead branch, the UI should surface the mismatch rather than hiding it.

### Branch Switching

Phase one should not support silent in-session branch switching as a background side effect of ordinary prompts.

If the user wants to work on a different branch, the product should prefer one of these explicit actions:

- open a new session on that branch
- explicitly rebind the current session to that branch

## Lead Agent Behavior

When `git_enabled` is true, the lead agent should know:

- the repository root
- the lead branch
- whether the working tree is clean or dirty
- whether detached HEAD applies

This context should be injected into the lead-agent system prompt or runtime context pack so the model knows what branch it is working against.

Phase one does not require the lead agent to automatically:

- stage files
- commit changes
- create branches
- merge branches

Instead, lead-agent edits continue to change the working tree directly, just as they do today. Git awareness informs planning, branch coordination, and user-facing state, but does not replace the current file-edit path.

## Subagent Relationship To Lead Branch

Subagents should inherit their Git parent context from the lead session:

- base repository root comes from the lead session
- base revision comes from the current lead workspace state
- the intended merge-back target is the lead branch recorded on the session

Subagents may still run in:

- `shared` mode for simple bounded tasks
- `worktree` mode for isolated branch work

For `worktree` mode, the branch and worktree are temporary execution artifacts attached to the subagent record, not replacements for the lead session branch.

## Merge-Back Model

Phase one should support explicit merge-back intent, not automatic merge execution.

The minimum useful behavior is:

- subagent returns its branch name
- subagent returns its worktree path
- subagent returns whether cleanup preserved or removed the worktree
- lead session knows the target lead branch that subagent work is conceptually meant to flow back into

What phase one should not do:

- auto-merge after subagent completion
- auto-cherry-pick isolated commits
- auto-resolve conflicts
- auto-commit lead working tree changes before merging

## User Experience

### Session Header

When `git_enabled` is true, the session header should show compact repository state:

- branch name
- dirty or clean status
- detached HEAD if applicable

### Branch Choice

If the workspace contains a Git repository with a current branch, the default lead branch is that current checkout branch.

If future UI adds branch choice, it should be explicit and attached to session creation or explicit rebind, not inferred from free-form prompts.

### Subagent Presentation

Subagent cards and summaries should continue to show:

- isolation mode
- subagent branch
- preserved worktree path when relevant

And the UI should make it clear that these branches are subordinate to the lead session branch, not peer lead contexts.

## Data Model

Recommended session-level additions:

- `repo_root`
- `git_enabled`
- `lead_branch`
- `head_revision`
- `working_tree_status`
- `detached_head`

Recommended derived values:

- `working_tree_status`: `clean`, `dirty`, `detached`, or `unknown`

Subagent records already carry branch and worktree execution metadata from the subagent worktree isolation design. No major change is required there beyond linking them conceptually to the lead session branch.

## Runtime Changes

Phase one requires:

- repository detection during session creation and explicit workspace rebind
- session state refresh of Git metadata on open or resume
- lead-agent runtime context injection of repository and branch state
- no change to ordinary `write_file` and `edit_file` execution semantics

The runtime should also be able to answer branch-aware questions such as:

- what branch am I on
- is the workspace dirty
- which branch is a subagent expected to merge back into

## Risks

### Risk: Branch Context Drift

The persisted lead branch may no longer match the current checkout if the repository changes outside Jarvis.

Mitigation:

- refresh Git metadata on session open
- surface mismatch clearly in the UI
- require explicit rebind if the user wants to adopt the new branch context

### Risk: Over-Automating Git Too Early

If the first Git-aware release also tries to commit or merge automatically, the product can damage user trust quickly.

Mitigation:

- keep lead-agent edits as working-tree changes in phase one
- make merge-back explicit

### Risk: Confusing Lead And Subagent Branch Roles

If the UI presents all branches the same way, users may misunderstand which branch is the primary working branch.

Mitigation:

- use "lead branch" language for the session
- use "subagent branch" language for isolated work

## Testing Requirements

- repository detection tests for Git and non-Git workspaces
- branch and dirty-state parsing tests
- session creation tests proving Git metadata is persisted when available
- runtime context tests proving lead branch metadata is injected for Git-backed sessions
- UI tests or render checks for session header Git state display

## Acceptance Criteria

- if the selected workspace is not a Git repository, Jarvis behaves as it does today
- if the selected workspace is a Git repository, the session persists lead repository and branch metadata
- the lead agent knows which branch it is working against
- subagent worktree branches remain explicitly associated with a lead session branch target
- no automatic stage, commit, or merge occurs in phase one
- dirty, clean, and detached states are surfaced clearly to the user
