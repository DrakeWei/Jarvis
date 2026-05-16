Date: 2026-05-14
Status: Approved in conversation, spec written for review

# Jarvis Dynamic Context, Long-Term Memory, Runtime Recovery, And Session Workspace Design

## Summary

Upgrade Jarvis from a short-window chat runtime into a session-grounded agent runtime with:

- session-level canonical workspace binding
- layered dynamic context assembly instead of a fixed recent-message slice
- session/workspace-scoped long-term memory
- durable turn lifecycle and recovery to a safe idle point after restart

The target is to move Jarvis closer to production-grade coding agents such as Codex and Claude Code on three fronts:

- dynamic context and long-term memory management
- seamless-enough runtime recovery after process restart
- session-level workspace restoration and path semantics

This phase deliberately does not target token-level stream continuation after restart. The recovery target is a safe idle point with clear recovery hints and resumable context.

## Goals

- Bind every live session to one canonical workspace
- Stop inferring the active workspace from each user prompt during normal execution
- Allow explicit external read references without silently mutating session workspace
- Replace the current fixed recent-message window with layered context assembly
- Persist session/workspace memory as structured summaries and facts
- Persist turn lifecycle and recovery checkpoints durably
- Recover interrupted work after restart to a safe idle point with useful resume hints
- Keep approval recovery aligned with turn recovery instead of storing runtime state in approval feedback

## Non-Goals

- Token-level or stream-level continuation after restart
- Cross-session or global user memory in this phase
- Full event-sourced runtime replay
- Multi-workspace execution inside one session
- Silent cross-workspace write execution
- Embedding-based vector memory retrieval in the first phase

## Confirmed Product Decisions

The design below follows these decisions confirmed in conversation:

- One session binds to one canonical workspace
- Runtime recovery target is recovery to a safe idle point
- Long-term memory in phase one is session/workspace scoped only
- External paths outside the session workspace may be read when explicitly referenced
- External writes outside the session workspace must not execute silently inside the current session

## Current State And Gaps

Today Jarvis already has durable storage for sessions, messages, events, tool executions, and approvals. It also restores pending approval runtime contexts on startup.

The main gaps are:

- the active workspace is effectively re-derived from each prompt instead of being a first-class session property
- turn execution is primarily an in-memory task map, not a durable lifecycle
- approval recovery is coupled to opaque runtime blobs stored in approval feedback
- context assembly is effectively a short recent-message slice
- there is no structured session/workspace memory state for long-running coding work

These gaps prevent reliable long-session behavior and make restart recovery fundamentally incomplete.

## Design Principles

- Session workspace is an invariant, not a guess
- Chat transcript and runtime state are different objects and must be modeled separately
- Recovery must prefer correctness over aggressive auto-resume
- Long-term memory should store compressed state, not duplicate raw transcript
- External references may inform one turn without redefining the session workspace
- Context must be assembled under an explicit budget, not by naive concatenation

## Approved Architecture

Introduce five stateful subsystems under the existing backend:

1. `WorkspaceBindingService`
   Owns session-to-workspace binding, path normalization, fingerprint validation, explicit rebinding, and path scope policy.

2. `TurnLifecycleManager`
   Owns turn creation, state transitions, checkpoints, interruption, cancellation, and failure handling.

3. `MemoryManager`
   Owns rolling summaries, structured memory facts, artifact references, memory salience, and memory compaction.

4. `ContextAssembler`
   Owns layered prompt assembly from system rules, session header, short-term working set, workspace facts, session memory, and current user input.

5. `RecoveryManager`
   Owns startup recovery of interrupted turns and pending approvals, and generates resume hints for restored sessions.

`RuntimeManager` remains the orchestration entry point, but stops being the source of truth for workspace, turn state, and recovery state.

## State Model

### Session

`Session` becomes the durable owner of user-visible long-running work context.

Add or formalize:

- `canonical_workspace_path`
- `workspace_fingerprint`
- `workspace_label`
- `session_status`
- `last_active_turn_id`

The session workspace is the default root for relative paths, tool execution, memory indexing, recovery, and UI restoration.

### Turn

`Turn` becomes the durable lifecycle object for one user input and the agent work it triggers.

Add or formalize:

- `session_id`
- `user_message_id`
- `workspace_path`
- `workspace_fingerprint`
- `status`
- `started_at`
- `updated_at`
- `completed_at`
- `last_checkpoint_seq`
- `resume_hint`
- `error_summary`

Recommended statuses:

- `queued`
- `running`
- `waiting_approval`
- `completed`
- `cancelled`
- `failed`
- `interrupted`

### TurnCheckpoint

`TurnCheckpoint` stores the minimal durable recovery payload needed to resume to a safe idle point.

Recommended fields:

- `turn_id`
- `checkpoint_seq`
- `phase`
- `workspace_path`
- `context_snapshot_ref` or `context_json`
- `pending_tool_name`
- `pending_tool_input_json`
- `summary`
- `created_at`

Checkpoints should be written at least:

- before model call
- after model output is received
- after a group of tool calls completes
- before entering approval wait
- before final turn completion

### SessionMemory

`SessionMemory` stores long-term memory for the current session and workspace only.

It should combine:

- one rolling session summary
- structured memory facts
- artifact and path references

Memory kinds should include at least:

- `goal`
- `constraint`
- `decision`
- `progress`
- `open_question`
- `artifact`

Suggested fields:

- `session_id`
- `kind`
- `content`
- `source_turn_id`
- `path_ref`
- `salience`
- `status`
- `created_at`
- `updated_at`

### WorkspaceSnapshot

`WorkspaceSnapshot` stores compact, derived workspace facts used for context assembly rather than user-facing transcript.

Typical contents:

- workspace root metadata
- key file paths
- README summary
- entrypoint and config summaries
- git status summary
- optional compact tree summary

## Workspace Scope Model

Every referenced path in a turn must be classified into one of four scope types before tool execution.

### Canonical Workspace

The session binds to one canonical workspace. This is the only default execution root for read and write operations.

### In-Workspace Reference

If a referenced path resolves inside the canonical workspace, it is normalized and treated as a standard in-workspace target. Reads and writes are allowed under existing tool policy.

### External Read Reference

If the user explicitly references a path outside the canonical workspace, the runtime may allow a read-only access for the current turn.

Rules:

- the path must be explicit
- the access is read-only
- it does not mutate session workspace
- it does not become default long-term project memory
- it may be logged as an external reference event for the turn

### Cross-Workspace Write Target

If the user requests writing outside the canonical workspace, the current session must not silently execute the write.

The runtime enters handoff with one of these outcomes:

- create a new session bound to the target workspace and execute there
- explicitly rebind the current session workspace
- refuse execution and answer with guidance

### Workspace Invariant

Inside one session, workspace may change only through explicit rebinding. It must never be implicitly mutated by ordinary prompt text.

## Dynamic Context Assembly

Replace the current fixed recent-message slice with a layered context pack assembled under an explicit token budget.

### Context Layers

Each model call should assemble context in this order:

1. `System Layer`
   Agent rules, tool boundaries, memory policy, recovery policy, and workspace policy.

2. `Session Header`
   Canonical workspace, workspace label, session status, current turn goal, unresolved constraints, and optional resume hint.

3. `Short-Term Working Set`
   Recent user and assistant turns, current turn plan, recent critical tool results, and current-turn external read references.

4. `Workspace Fact Pack`
   Derived workspace facts such as key paths, README summary, entrypoint summary, relevant config summary, and recent high-value file facts.

5. `Session Memory Pack`
   Structured long-term memory for goals, constraints, decisions, progress, open questions, and important artifact references.

6. `Current Turn Input`
   The latest user message.

### Context Budgeting

Context must be assembled under an explicit budget rather than concatenated until it fits.

Suggested initial budget split:

- 15 percent system and session header
- 35 percent short-term working set
- 20 percent workspace fact pack
- 20 percent session memory pack
- 10 percent response headroom

### Compaction Order

When the context pack exceeds budget, compact in a deterministic order:

1. drop low-value external read references
2. replace large tool outputs with tool summaries
3. collapse older short-term turns into turn summaries
4. drop low-salience memory entries
5. emit a context compaction event when heavier compaction occurs

## Long-Term Memory Management

The first phase should use deterministic, structured memory rather than vector retrieval.

### Memory Extraction

After each completed, interrupted, failed, or waiting-approval turn, extract and persist:

- active goals
- constraints
- decisions
- progress
- open questions
- important artifacts and paths

### Memory Exclusions

Do not store as long-term memory:

- small talk
- low-confidence guesses
- stale transient reasoning
- large raw tool output
- unrelated external reference contents

### Retrieval Strategy

Memory retrieval should be deterministic and explainable:

- filter by memory kind
- prioritize active and unresolved items
- boost by recency
- boost by overlap with referenced paths, modules, and goals in the current turn
- cap the number of entries per memory kind

### Rolling Summaries

Maintain two summaries:

- `Rolling Session Summary`
  The current durable summary of what the session is doing, what is decided, and what remains.

- `Last Interrupted Turn Summary`
  A compact summary of what was in progress when the runtime stopped and what should happen next.

## Turn Lifecycle And Runtime Recovery

### Turn State Machine

Recommended turn lifecycle:

1. create turn as `queued`
2. transition to `running` when execution begins
3. transition to `waiting_approval` when a gated tool must pause
4. transition to `completed` on success
5. transition to `cancelled` on user stop
6. transition to `failed` on unrecovered runtime failure
7. transition to `interrupted` during startup recovery for any turn that was still `running`

### Startup Recovery

On backend startup:

- scan all `running` turns and mark them `interrupted`
- scan all `waiting_approval` turns and rehydrate their approval UI state
- generate or refresh `resume_hint` for interrupted turns
- emit timeline events that the session recovered to a safe idle point

### Safe Recovery Contract

Recovery target is not token-level continuation. The contract is:

- the session remembers its workspace
- the session remembers what it was trying to do
- the session remembers the last completed tool results and current pending state
- the next turn can continue from a compact recovery summary

### Approval Recovery Refactor

Approval state should stop carrying opaque runtime blobs in user-facing approval fields.

Instead:

- approval records reference `turn_id`
- approval records reference `checkpoint_seq` or `checkpoint_id`
- checkpoint records store the durable recovery context

When approval is granted:

- load the referenced checkpoint
- rehydrate the turn working context
- continue from the next safe execution step

When approval is rejected:

- persist the rejection result into turn context
- mark the turn as `interrupted` or `completed` depending on policy
- allow the next user turn to decide how to proceed

### Tool Idempotency And Recovery

Recovery must avoid replaying uncertain side effects.

Classify tools into:

- safely replayable read operations
- write or side-effect operations that must not be replayed blindly

For completed tool calls:

- prefer reusing persisted tool results over re-executing

For uncertain side-effect boundaries:

- prefer interrupting to a safe idle point rather than guessing and replaying

## Database Changes

### Extend `sessions`

Add:

- `canonical_workspace_path`
- `workspace_fingerprint`
- `workspace_label`
- `status`
- `last_active_turn_id`

### Extend `turns`

Formalize `turns` as an actively used runtime table and add:

- `user_message_id`
- `workspace_path`
- `workspace_fingerprint`
- `updated_at`
- `last_checkpoint_seq`
- `resume_hint`
- `error_summary`

### Add `turn_checkpoints`

Suggested fields:

- `id`
- `turn_id`
- `checkpoint_seq`
- `phase`
- `context_json`
- `pending_tool_name`
- `pending_tool_input_json`
- `summary`
- `created_at`

### Add `session_memory`

Suggested fields:

- `id`
- `session_id`
- `kind`
- `content`
- `salience`
- `status`
- `source_turn_id`
- `path_ref`
- `created_at`
- `updated_at`

### Optional `workspace_snapshots`

Suggested fields:

- `id`
- `session_id`
- `root_path`
- `snapshot_kind`
- `content`
- `created_at`

### Extend `approvals`

Add:

- `turn_id`
- `checkpoint_seq` or `checkpoint_id`

## API And UI Implications

Recommended additions:

- `GET /sessions/{id}/state`
  Returns session status, canonical workspace, last interrupted summary, and pending turn metadata.

- `GET /turns/{id}`
  Returns turn lifecycle state and checkpoint summary.

- `POST /turns/{id}/resume`
  Marks a turn as ready for the next safe continuation path.

- `POST /sessions/{id}/rebind-workspace`
  Explicitly rebinds the session to a new canonical workspace.

Frontend changes should include:

- visible session status such as `running`, `waiting approval`, and `interrupted`
- a recovery banner above the composer for interrupted sessions
- canonical workspace display in session header
- approval UI bound to turn and checkpoint state rather than opaque feedback blobs

## Migration Order

Implement in four steps rather than one large rewrite.

### Step 1: Session Workspace Grounding

- add canonical workspace fields to sessions
- resolve workspace at session creation or explicit rebind time
- stop using prompt text as the source of truth for per-turn workspace selection
- keep external read reference support under the new scope model

### Step 2: Durable Turn Lifecycle

- make every user message create a durable turn
- drive runtime state transitions through the turns table
- use in-memory active turn handles only as execution caches
- add startup recovery for `running` and `waiting_approval` turns

### Step 3: ContextAssembler And SessionMemory

- replace the fixed recent-message window
- add rolling session summary and structured memory facts
- add deterministic retrieval and compaction

### Step 4: Approval Refactor And Recovery UX

- replace approval feedback runtime blobs with checkpoint references
- rehydrate approval state through turn and checkpoint records
- add recovery state to session UI and timeline

## Acceptance Criteria

- each live session has exactly one canonical workspace
- relative paths resolve only against the session workspace
- explicit external paths outside the session workspace can be read but do not mutate the session workspace
- writes outside the session workspace require handoff or explicit rebinding
- long conversations no longer rely only on the latest recent-message slice
- rolling session summary and structured session memory are persisted
- restart converts unfinished `running` turns into `interrupted` turns with resume hints
- pending approval turns survive restart and remain actionable
- approval recovery does not depend on storing runtime blobs in approval feedback

## Risks And Mitigations

- Risk: session creation without a clear workspace binding.
  Mitigation: require explicit workspace resolution at session creation time, or keep the session in an unbound draft state until the workspace is known.

- Risk: memory bloat from storing too much raw detail.
  Mitigation: store compressed facts and summaries, not raw transcript or large tool outputs.

- Risk: accidental replay of side effects during recovery.
  Mitigation: reuse persisted tool results when possible and prefer safe interruption to speculative replay.

- Risk: user confusion when a prompt mentions another project.
  Mitigation: surface the workspace mismatch flow clearly with external-read and cross-workspace-write semantics.

## Out Of Scope But Naturally Enabled Later

This design keeps the door open for later:

- user-level cross-session memory
- embedding or vector retrieval
- richer background job recovery
- more advanced multi-agent recovery
- stronger idempotency models per tool
