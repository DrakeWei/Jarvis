# Jarvis Context Assembler, Deterministic Memory Retrieval, Internal Memory Tools, And Runtime Compaction Design

Date: 2026-05-15
Status: Approved in conversation, spec written for review

## Summary

Upgrade Jarvis from a fixed recent-message context builder into a layered runtime context system with deterministic session memory retrieval, stable and dynamic context separation, agent-internal memory search tools, and runtime-only context compaction under a bounded budget.

This phase preserves the current durable runtime model:

- `sessions` remain the owner of workspace identity
- `turns` remain the durable execution lifecycle
- `turn_checkpoints` remain the recovery source of truth
- `messages` remain the durable transcript
- approval and recovery semantics do not change

## Goals

- Replace the fixed recent-message slice with a layered context assembler
- Retrieve session memory deterministically instead of concatenating a flat header
- Separate low-churn stable context from high-churn turn context
- Expose `memory_search` and `conversation_search` as agent-internal tools
- Add runtime-only budget enforcement and compaction before model calls
- Preserve current checkpoint and resume semantics

## Non-Goals

- Embedding or vector retrieval
- L0/L1/L2/L3 memory pipelines
- Persona, scene, or MMD systems
- Mutating durable transcript content for compaction
- Checkpoint schema redesign
- UI exposure of memory tools in this phase

## Current State

Jarvis currently assembles model context from:

- a static system prompt
- the last 8 durable transcript messages
- a plain-text `Session memory:` header built from active memory rows

This has three problems:

1. context is not budgeted by value
2. stable and dynamic state are mixed together
3. the model cannot actively search prior memory or transcript when the initial pack is insufficient

Jarvis already has strong durable runtime primitives:

- `SessionRecord`
- `TurnRecord`
- `TurnCheckpointRecord`
- `SessionMemoryRecord`

This phase should build on those primitives rather than introducing a parallel memory runtime.

## Design Principles

- Runtime context selection must be isolated from durable transcript state.
- Retrieval must be deterministic and explainable.
- Compaction must be reversible by regeneration, not by mutating saved data.
- Stable context should churn less than dynamic context.
- Current user intent and current turn continuity outrank old history.
- The first implementation should favor bounded complexity over maximum recall quality.

## Proposed Architecture

Introduce a new backend context assembly layer with five focused components:

1. `SessionMemoryRetriever`
   Reads `session_memory` and returns ranked entries by kind, status, overlap, salience, and recency.

2. `ShortTermContextBuilder`
   Builds the short-term working set from the in-loop message list and recent tool results.

3. `WorkspaceFactBuilder`
   Produces lightweight workspace facts for the current turn:
   - canonical workspace path
   - workspace mode
   - explicit external read references
   - recent artifact paths already recorded in session memory

4. `ContextBudgetManager`
   Applies a runtime-only size budget and deterministic compaction sequence.

5. `ContextAssembler`
   Orchestrates the builders above and returns the final model-facing context pack.

`RuntimeManager` keeps ownership of the agent loop, tool execution, checkpoints, approvals, and recovery. The new layer only changes how model input is assembled immediately before a model call.

## Runtime Data Flow

### Durable flow that stays unchanged

1. user message is persisted
2. turn record is created
3. rule-based memory signals are persisted
4. runtime loop begins
5. checkpoints are written across loop phases
6. durable transcript remains the source of truth for resume

### New context assembly flow

Before each model call inside `_continue_agent_loop(...)`:

1. `RuntimeManager` passes the current in-loop `messages`, session id, workspace, and allowed external reads into `ContextAssembler`.
2. `ContextAssembler` requests:
   - ranked session memory from `SessionMemoryRetriever`
   - short-term working context from `ShortTermContextBuilder`
   - workspace facts from `WorkspaceFactBuilder`
3. `ContextBudgetManager` computes the allowed budget and compacts only the model-facing representation.
4. `ContextAssembler` returns:
   - `system_prompt_stable`
   - `model_messages`
   - `debug_meta`
5. `_stream_agent_response(...)` sends only the assembled pack to the model.
6. The original in-loop `messages` list remains unchanged and continues to be the object written into checkpoints.

The separation between assembled context and durable loop state is the core safety boundary for this phase.

## Stable And Dynamic Context Split

The model-facing context should be split into two categories.

### Stable system context

Low-frequency, reusable, lower-churn content:

- agent rules
- workspace policy
- canonical workspace path
- workspace mode
- current high-priority constraints
- a small number of active goals
- recent confirmed decisions
- recent artifact references

Stable context goes at the end of the system prompt so that later compaction can avoid disturbing it.

### Dynamic turn context

High-frequency, turn-relevant content:

- current user input
- recent short-term turns
- latest progress
- open questions
- prompt-matched session memory
- current-turn external read references
- current-turn tool results or their compact summaries

Dynamic context is emitted as explicit runtime blocks inside `model_messages`, not as a large appended system header.

## Deterministic Session Memory Retrieval

`SessionMemoryRetriever` should use only existing `session_memory` rows and deterministic ranking. No vector index is added in this phase.

### Ranking signals

Rows should be ranked using the following ordered signals:

1. `status`
   `active` outranks `archived`, and `archived` outranks `resolved`

2. `kind priority`
   `constraint > goal > decision > progress > open_question > artifact`

3. `path overlap`
   If the current user input, recent tool inputs, or recent artifact paths mention the same path segment or file name as `path_ref`, boost the row

4. `text overlap`
   If the current user input contains normalized substrings that match the row content, boost the row

5. `salience`

6. `recency`

### Per-kind caps

The first implementation should cap retrieved rows per kind:

- `constraint`: 2
- `goal`: 2
- `decision`: 3
- `progress`: 2
- `open_question`: 2
- `artifact`: 4

### Stable vs dynamic placement

- Stable placement:
  `constraint`, durable `goal`, and recent `decision` rows
- Dynamic placement:
  `progress`, `open_question`, path-matched `artifact`, and prompt-matched rows from any kind

This split ensures that current-turn relevance can churn without forcing the entire system prompt to change.

## Short-Term Context Builder

`ShortTermContextBuilder` replaces the current fixed “last 8 messages” behavior.

The first implementation should build a bounded short-term working set from:

- the latest user input
- the latest assistant action or answer
- the latest tool result
- additional recent turns in recency order while budget remains

It should prefer turns that contain:

- tool use
- tool results
- explicit file paths
- current workspace references
- active decision points

Purely repetitive or low-information conversational turns should be the first candidates for omission.

## Internal Memory Tools

Add two agent-internal tools. They are callable by the model but not exposed as new UI features in this phase.

### `memory_search`

Backed by `session_memory`.

Input:

- `query` required
- `kind` optional
- `limit` optional

Output:

- concise text result for the model
- each item includes `kind`, `content`, optional `path_ref`, and `source_turn_id`

### `conversation_search`

Backed by the durable `messages` transcript for the current session.

Input:

- `query` required
- `role` optional
- `limit` optional

Output:

- concise text result for the model
- each item includes `role`, excerpted `content`, and ordering context

These tools are intended as recovery valves when the initial context pack is insufficient. They complement retrieval and compaction instead of replacing them.

## Runtime Budgeting And Compaction

This phase adds compaction only to the runtime representation sent to the model.

It does not:

- alter `messages`
- alter `turn_checkpoints`
- alter approval resume state
- alter durable transcript history

### Budget source

The first implementation should derive the budget from `settings.llm_max_tokens` and use a character-count approximation rather than a tokenizer dependency.

Budget policy:

- reserve fixed response headroom
- reserve a stable-system slice
- allocate the remaining budget to dynamic context

### Compaction order

If the assembled context exceeds budget, compact in this order:

1. drop low-value external read references
2. compress old artifact references
3. drop low-priority session memory rows
4. replace large tool results with deterministic summary blocks
5. fold older short-term turns
6. trim oldest dynamic helper blocks while always preserving:
   - current user input
   - latest assistant action
   - latest tool result
   - active constraints
   - current goal

### Tool result compaction

The first implementation should not call another model to summarize tool results.

Instead it should build deterministic summaries using:

- tool name
- execution status
- head excerpt
- tail excerpt when needed
- file/path lines when detectable
- line count or size hint when useful

If a tool result is compacted, the summary should explicitly say that the full result was shortened and that the model may re-read or search if more detail is needed.

## Integration Shape

The first implementation should minimize invasive change by keeping `RuntimeManager` as the outer coordinator.

### RuntimeManager changes

- Replace direct use of `context_service.build_turn_messages(...)` in the lead path
- Call `ContextAssembler.assemble(...)` before each model call inside `_continue_agent_loop(...)`
- Pass assembled `system` and `messages` into `_stream_agent_response(...)`
- Keep checkpoints pointed at the original in-loop `messages`

### New service/module boundaries

Suggested modules:

- `backend/app/services/context_assembler.py`
- `backend/app/services/memory_retriever.py`
- `backend/app/services/conversation_search_service.py`
- `backend/app/services/memory_search_service.py`
- `backend/app/services/context_budget.py`

`context_service.py` may remain as a thin compatibility layer or be reduced to helpers shared by the new assembler.

### Tool registration

Register `memory_search` and `conversation_search` in the same internal tool path used by the existing autonomous tool set so they are available only to the runtime agent.

## Error Handling And Degradation

This phase must degrade safely.

- If retrieval fails, continue with short-term context only.
- If compaction fails, fall back to un-compacted assembled context.
- If assembled context still exceeds safe limits, return the smallest guaranteed-safe pack rather than aborting the turn.
- If internal memory tools fail, return a concise error result to the model so the loop can continue.

The runtime should prefer reduced recall over hard failure.

## Observability

`ContextAssembler` should return `debug_meta` with at least:

- original estimated size
- final estimated size
- dropped block categories
- summarized tool result count
- retrieved memory counts by kind

The first implementation only needs backend logs. No UI surface is required.

## Testing Strategy

Add focused tests for:

1. deterministic memory ranking
2. path overlap boosting
3. stable vs dynamic placement
4. short-term context selection
5. compaction ordering
6. preservation of current user input and latest tool result under compaction
7. `memory_search` tool formatting
8. `conversation_search` tool formatting
9. runtime recovery invariance:
   checkpoints still contain the original loop messages, not the compacted pack

Manual verification should include:

- ordinary short chat request
- tool-heavy coding turn with long outputs
- resumed interrupted turn
- waiting-approval turn that resumes after approval
- prompt that requires the model to call `memory_search` or `conversation_search`

## Rollout Order

Implement in this order:

1. deterministic retrieval
2. stable and dynamic split
3. internal memory tools
4. runtime-only budget manager and compaction

This ordering keeps the highest-value retrieval improvements available before the more invasive runtime compaction work lands.
