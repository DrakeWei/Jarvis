# Jarvis Memory, Recovery, And Workspace Implementation Plan

Date: 2026-05-15
Depends on: `docs/superpowers/specs/2026-05-14-jarvis-memory-recovery-workspace-design.md`

## Delivery Strategy

Implement the design in five phases:

1. Session workspace grounding
2. Durable turn lifecycle foundation
3. Context assembler and session memory foundation
4. Approval recovery refactor
5. Recovery UX and verification

The immediate execution focus is Phase 1 so Jarvis stops treating prompt text as the source of truth for workspace selection.

## Phase 1: Session Workspace Grounding

### Objectives

- Bind every session to one canonical workspace
- Make runtime execution use the session workspace by default
- Support explicit external absolute-path reads without mutating session workspace
- Block silent cross-workspace writes

### Tasks

- Extend `sessions` persistence and API schemas with:
  - `canonical_workspace_path`
  - `workspace_fingerprint`
  - `workspace_label`
  - `status`
- Add database migration for the new session fields
- Add a workspace-binding utility for:
  - path normalization
  - fingerprint generation
  - session default workspace binding
- Refactor runtime turn execution to load the session workspace instead of resolving a new workspace from each prompt
- Add explicit path policy for tool execution:
  - in-workspace reads and writes allowed
  - explicit external absolute-path reads allowed
  - explicit external writes blocked with a user-facing handoff message
- Add minimal API and frontend support to surface the canonical workspace

### Exit Criteria

- New and existing sessions have a stable canonical workspace
- Lead turns run against the session workspace
- Explicit external file reads work without rebinding the session
- Cross-workspace writes do not silently execute in the current session

## Phase 2: Durable Turn Lifecycle Foundation

### Objectives

- Persist turn state as a first-class runtime lifecycle
- Make restart recovery able to mark interrupted turns reliably

### Tasks

- Expand active use of `turns`
- Add missing turn state fields and migration
- Create turn records for user messages before execution
- Move runtime state transitions through the turns table
- Add startup recovery for `running` and `waiting_approval` turns

### Exit Criteria

- Each user message creates a durable turn
- Unfinished turns are visible and recoverable after restart

## Phase 3: Context Assembler And Session Memory Foundation

### Objectives

- Replace the fixed recent-message window
- Persist compact session/workspace memory

### Tasks

- Add `session_memory`
- Add rolling session summary generation
- Build layered context assembly with explicit budget handling
- Add deterministic memory retrieval and compaction

### Exit Criteria

- Long sessions no longer rely only on the most recent messages
- Session goals, constraints, and progress persist across turns

## Phase 4: Approval Recovery Refactor

### Objectives

- Decouple approval storage from opaque runtime context blobs
- Align approval recovery with turn checkpoints

### Tasks

- Add checkpoint references to approvals
- Move recovery payload into turn checkpoints
- Refactor approval resume flow to hydrate from turn/checkpoint state

### Exit Criteria

- Approval recovery no longer depends on hidden feedback payloads

## Phase 5: Recovery UX And Verification

### Objectives

- Make interrupted-session recovery visible and understandable
- Verify workspace, memory, and recovery behavior end to end

### Tasks

- Add recovery banner and session status UI
- Add session state API for canonical workspace and recovery hints
- Verify:
  - session workspace binding
  - external read behavior
  - blocked external writes
  - interrupted-turn recovery
  - approval survival across restart

### Exit Criteria

- Recovery state is visible in UI
- Core workspace and recovery flows are reproducible locally

## Sequencing Notes

- Phase 1 should land before any memory or recovery work, because later phases depend on a stable session workspace invariant
- Phase 2 can begin once Phase 1 stops using prompt text as the runtime workspace truth
- Phase 3 should follow only after turn state is durable enough to support summary refreshes and recovery hints
- Phase 4 and Phase 5 should not start until the new turn and checkpoint boundaries are stable

## Risks To Watch During Implementation

- Letting Phase 1 quietly preserve prompt-based workspace switching in edge paths
- Accidentally permitting external writes by over-broad absolute-path handling
- Returning external-read support without clearly separating it from session workspace identity
- Growing the implementation into a full runtime rewrite instead of a staged migration

## Workspace UX Addendum

The original implementation direction assumed every session would bind to a canonical workspace immediately. Follow-up product decisions changed that into a mixed model:

- bound sessions created from an explicit folder picker
- default sessions grouped under a default conversation drawer with broader read scope
- auto-bind from the first message still allowed when the user did not manually select a folder
- first cross-directory write from a default session should prompt the user and offer conversion into a bound session

Implementation implications:

- sessions need an explicit workspace mode rather than assuming every session is always bound
- the left rail should group sessions by drawer:
  - one drawer per bound workspace
  - one default drawer for unbound sessions
- session creation needs a folder-picker-assisted path in the frontend, not only implicit binding
- runtime policy must distinguish:
  - bound session
  - default session
  while preserving recovery and memory semantics
