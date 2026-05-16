# Jarvis Session Attachments Implementation Plan

Date: 2026-05-16
Depends on: `docs/superpowers/specs/2026-05-16-jarvis-session-attachments-design.md`

## Delivery Strategy

Implement the design in six phases:

1. Session asset persistence foundation
2. Asset upload and ingestion pipeline
3. Composer attachments UX
4. Runtime and provider integration
5. Asset retrieval tools and replay integration
6. Verification and hardening

The immediate execution focus is Phase 1 so attachments become first-class session resources before any UI or provider work depends on them.

## Phase 1: Session Asset Persistence Foundation

### Objectives

- Introduce durable session asset storage outside the workspace tree
- Add database models and lightweight migrations for asset metadata and message associations
- Establish a stable on-disk storage layout for raw and derived asset files

### Tasks

- Add new persistence models for:
  - `session_assets`
  - `message_assets`
  - `asset_chunks`
- Extend database initialization and lightweight migration helpers to create or evolve the new tables
- Add a session asset storage utility responsible for:
  - asset ID allocation
  - per-session asset directory resolution
  - raw file path allocation
  - derived artifact path allocation
- Define canonical asset states:
  - `uploaded`
  - `processing`
  - `ready`
  - `failed`
  - `removed`
- Add backend schemas for asset summaries, asset details, and message asset references

### Exit Criteria

- New asset tables are created on startup
- Uploaded files can be assigned durable asset records and storage locations
- The storage boundary between session assets and workspace files is explicit in code

## Phase 2: Asset Upload And Ingestion Pipeline

### Objectives

- Accept file uploads through the API without blocking on full parsing
- Run asynchronous ingestion to produce metadata, previews, and document chunks

### Tasks

- Add API routes for:
  - asset upload
  - list assets in a session
  - get asset detail
  - delete or hide an asset
- Implement backend validation for:
  - MIME type
  - extension compatibility
  - per-file size limit
  - per-request count limit
- Write raw uploads into session asset storage
- Create an ingestion service that can:
  - mark an asset as `processing`
  - branch by asset kind
  - write extracted metadata and derived artifacts
  - chunk PDF and Office text for retrieval
  - mark success or failure with a user-facing error message
- Emit session timeline events for:
  - asset uploaded
  - asset processed
  - asset failed

### Exit Criteria

- A file upload returns quickly with an asset record
- Ingestion can complete independently of the upload response
- Failed parsing does not corrupt the asset record or block later turns

## Phase 3: Composer Attachments UX

### Objectives

- Let users add, inspect, and remove attachments in the frontend composer
- Surface asset lifecycle states clearly before and after message send

### Tasks

- Extend the frontend API client for:
  - upload asset
  - list session assets
  - fetch asset detail
  - delete asset
- Upgrade the composer UI to support:
  - file picker
  - drag and drop
  - attachment tray
  - per-asset remove action
  - loading and failed states
- Add minimal visual treatment for:
  - image thumbnails
  - PDF and Office file cards
- Extend message submission so a user message can reference `asset_ids`
- Make draft-session handling preserve attached asset state if the first send creates the real session lazily

### Exit Criteria

- A user can upload one or more files from the composer
- The composer shows attachment readiness before send
- Sent messages can reference uploaded assets without embedding file contents into the textarea

## Phase 4: Runtime And Provider Integration

### Objectives

- Make runtime execution asset-aware
- Support direct image input and selective document chunk injection

### Tasks

- Extend message schemas so runtime can accept:
  - text
  - asset references
- Refactor session message persistence to preserve attachment references per user message
- Update context assembly to include:
  - ready image inputs
  - compact summaries for attached documents
  - retrieved document chunks relevant to the current request
- Add provider input block types for:
  - text
  - image
  - document chunk
- Extend the OpenAI adapter so image assets are converted into valid multimodal input items
- Keep PDF and Office inputs text-based in v1 by using extracted chunks instead of sending full raw files to the model
- Define fallback behavior when:
  - an asset is still processing
  - an asset failed ingestion
  - a provider does not support image input

### Exit Criteria

- A message with an attached image can be answered multimodally when the provider supports it
- A message with an attached document uses extracted summaries or chunks instead of whole-file injection
- Asset failures degrade gracefully instead of breaking the turn

## Phase 5: Asset Retrieval Tools And Replay Integration

### Objectives

- Make large documents inspectable without prompt bloat
- Preserve attachment-aware execution in replay, recovery, and session state views

### Tasks

- Add session asset tools such as:
  - `list_session_assets`
  - `read_asset_summary`
  - `search_asset_chunks`
  - `read_asset_chunk`
- Integrate asset references into:
  - turn checkpoints
  - replay context
  - session state summaries where useful
- Ensure timeline output records high-value asset lifecycle events without dumping extracted content
- Add cleanup rules so deleting or hiding a session asset updates user-visible state consistently

### Exit Criteria

- The model can request more document detail through retrieval tools instead of relying only on initial prompt injection
- Turn replay preserves which assets and chunks were in scope
- Timeline noise stays bounded

## Phase 6: Verification And Hardening

### Objectives

- Prove the main attachment flows end to end
- Tighten validation, error handling, and cleanup semantics

### Tasks

- Add backend tests for:
  - asset model creation
  - upload validation
  - chunk generation
  - message-to-asset associations
- Add runtime tests for:
  - image input assembly
  - document chunk injection
  - fallback on failed or processing assets
- Add frontend verification for:
  - composer upload interactions
  - asset tray state transitions
  - message send with attachments
- Verify session cleanup behavior for removed assets and deleted sessions
- Verify mixed attachment sessions locally:
  - image only
  - PDF only
  - Office only
  - mixed image plus document

### Exit Criteria

- Core attachment flows are reproducible locally
- Invalid files fail safely and clearly
- Session cleanup leaves no user-visible orphaned asset state

## Sequencing Notes

- Phase 1 must land before any ingestion or UI work so every upload has a durable home
- Phase 2 should land before Phase 3 so the composer can bind to a stable backend contract
- Phase 4 should begin only after the upload and state model are stable enough to avoid provider-specific rework
- Phase 5 should follow Phase 4 because replay and retrieval depend on the final runtime asset shape
- Phase 6 should run continuously, but full end-to-end verification should wait until Phases 3 through 5 are functionally complete

## Risks To Watch During Implementation

- Smuggling large document bodies into prompt assembly before retrieval boundaries are enforced
- Coupling session assets back to workspace paths and losing the storage boundary
- Letting draft-session creation race with asset uploads or asset-to-message references
- Over-designing Office parsing beyond what v1 needs for reliable retrieval
