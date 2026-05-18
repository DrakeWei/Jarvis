# Jarvis Multimodal Capabilities Implementation Plan

Date: 2026-05-18
Depends on: `docs/superpowers/specs/2026-05-18-jarvis-multimodal-capabilities-design.md`

## Delivery Strategy

Implement the multimodal plan in seven phases:

1. Persistence, schema, and provider configuration foundation
2. Audio and video ingestion pipeline
3. Runtime, context assembly, and tool expansion
4. Frontend multimodal attachment UX
5. Image generation provider formalization
6. Streaming speech synthesis
7. Video generation framework and hardening

The first execution target is still the understanding loop:

- image direct understanding
- audio transcription and retrieval
- video keyframe plus transcription understanding
- stable multimodal asset rendering in the frontend

Speech synthesis and video generation should be scaffolded in the plan, but should not block the first production milestone.

## Phase 1: Persistence, Schema, And Provider Configuration Foundation

### Objectives

- Extend the asset model to represent multimodal uploads and generated outputs
- Add the minimal schema needed for time-based media chunks and provider-neutral metadata
- Establish configuration and factory seams before any provider-specific implementation lands

### Tasks

- Extend `session_assets` persistence with:
  - `origin`
  - `source_asset_id`
  - `metadata_json`
- Extend `asset_chunks` persistence with:
  - `start_ms`
  - `end_ms`
  - `speaker`
  - `frame_index`
  - `frame_timestamp_ms`
- Expand allowed asset kinds in backend schemas and validation:
  - `audio`
  - `video`
  - `generated_image`
  - `generated_audio`
  - `generated_video`
- Expand asset status vocabulary to include:
  - `queued`
  - `partial`
  - `deleted`
- Add lightweight migrations in database bootstrap code for all new fields
- Extend `backend/app/core/config.py` with provider-scoped multimodal settings for:
  - image generation
  - TTS
  - ASR
  - video generation placeholders
- Split provider factory responsibilities into capability-aware constructors instead of a single `create_client()`
- Introduce provider interface types and request/response dataclasses under `backend/app/providers/`

### Exit Criteria

- database bootstrap can create or upgrade the new multimodal fields
- asset schemas can represent uploaded and generated audio and video
- provider selection is no longer hard-wired to a single text adapter

## Phase 2: Audio And Video Ingestion Pipeline

### Objectives

- Accept audio and video files through the existing asset upload path
- Produce derived transcript and keyframe artifacts that can be searched and injected into context

### Tasks

- Extend upload validation in `asset_ingestion_service` to recognize supported audio and video MIME types and extensions
- Add metadata extraction helpers for:
  - duration
  - width and height
  - frame rate when available
  - channel count when available
- Add `speech_recognition_service` with a provider-neutral transcript result shape
- Add `video_understanding_service` orchestration that:
  - extracts or stages audio from video
  - invokes ASR on the audio track
  - extracts keyframes at bounded intervals
  - emits compact visual summaries for keyframes
- Persist transcript segments into `asset_chunks`
- Persist keyframe summaries into `asset_chunks`
- Write internal derived artifacts beside the source asset without promoting all of them to user-visible assets
- Add explicit job types and status transitions for:
  - `asset_transcribe_audio`
  - `asset_ingest_video`
- Emit timeline events for:
  - processing started
  - partial availability
  - ready
  - failed

### Exit Criteria

- uploaded audio can be transcribed into searchable chunks
- uploaded video can produce transcript chunks and keyframe summaries
- failed media parsing or transcription does not corrupt the original asset record

## Phase 3: Runtime, Context Assembly, And Tool Expansion

### Objectives

- Make the turn loop understand audio and video attachments without prompt bloat
- Keep runtime logic orchestration-only and move provider-specific behavior into services

### Tasks

- Extend `context_assembler` attachment expansion rules:
  - images remain direct `input_image`
  - audio expands to asset summary plus retrieved transcript chunks
  - video expands to asset summary, transcript chunks, and a small bounded set of keyframes when needed
- Extend asset summary formatting to include timecode information from chunk metadata
- Update runtime attachment messaging so assistant replies can carry generated audio and video asset ids
- Add generation tools to the MCP registry:
  - `generate_speech`
  - `generate_video`
- Keep inspection tools generic:
  - `list_session_assets`
  - `read_asset_summary`
  - `search_asset_chunks`
  - `read_asset_chunk`
- Ensure tool outputs can return generated asset ids in the same way image generation already does
- Update system prompt guidance so the lead agent knows:
  - uploaded audio and video should be inspected through asset tools
  - speech generation should be used only when the user asks for audio output
  - video generation should be used only when the user asks for video output
- Add graceful fallback behavior for:
  - assets still processing
  - failed media ingestion
  - providers unavailable or unconfigured

### Exit Criteria

- a user message with audio or video attachments can be answered from derived context
- multimodal tool results can attach generated asset ids to assistant replies
- missing media providers fail clearly without breaking unrelated text turns

## Phase 4: Frontend Multimodal Attachment UX

### Objectives

- Extend the existing attachment tray and timeline so audio and video feel native
- Keep the frontend model aligned with the backend asset schema instead of using filename heuristics

### Tasks

- Extend `frontend/src/lib/api.ts` asset types with:
  - `origin`
  - `source_asset_id`
  - `metadata_json`
- Extend upload selection to allow `audio/*` and `video/*`
- Render audio attachments with:
  - status
  - duration when available
  - inline player for ready assets
- Render video attachments with:
  - cover or preview image
  - duration when available
  - inline player or poster-plus-open behavior
- Add generated asset visual treatment:
  - generated image badge
  - generated audio badge
  - generated video badge
- Surface `queued`, `processing`, `partial`, `ready`, and `failed` states consistently in:
  - composer tray
  - timeline cards
  - asset detail surfaces if added later
- Verify drag and drop, upload, delete, and send interactions still work for draft sessions and persisted sessions

### Exit Criteria

- a user can upload audio and video from the current composer
- ready audio and video assets are playable from the conversation UI
- generated multimodal outputs are visually distinct from uploaded assets

## Phase 5: Image Generation Provider Formalization

### Objectives

- Convert the current image generation path into a capability-provider implementation instead of a one-off service
- Preserve compatibility with the AIDP OpenAI-compatible image generation and edit endpoints

### Tasks

- Extract provider-neutral request and result shapes for image generation and image editing
- Implement an AIDP OpenAI-compatible image provider that supports:
  - `images/generations`
  - `images/edits`
- Map existing image generation settings onto the provider:
  - `model`
  - `size`
  - `quality`
  - `background`
  - optional source images
  - optional mask asset
- Preserve the current session asset write-back behavior:
  - persist generated image bytes
  - create `generated_image` asset
  - attach metadata for prompt, size, quality, provider
- Keep the existing `generate_image` tool contract stable unless a change is required for consistency
- Add provider-level error normalization so user-visible failures do not expose raw transport details

### Exit Criteria

- image generation continues to work through the existing conversation flow
- AIDP endpoint configuration is driven entirely by config
- generated images are stored as first-class session assets with richer metadata

## Phase 6: Streaming Speech Synthesis

### Objectives

- Add assistant audio output through streaming TTS
- Keep the generated audio lifecycle compatible with the session asset model and timeline

### Tasks

- Add `speech_generation_service` with request and result types for:
  - text
  - voice
  - format
  - speed
  - pitch
  - stream mode
- Implement a Volcengine V3 speech synthesis provider for streaming output
- Add a chunk event model that can carry:
  - sequence
  - audio bytes
  - format
  - final chunk indicator
  - optional subtitle or timing metadata
- Persist streaming output into a `generated_audio` asset:
  - mark asset `queued`
  - mark asset `partial` during streaming
  - mark asset `ready` on completion
  - mark asset `failed` on transport or provider failure
- Add the `generate_speech` tool and connect it to assistant turns
- Ensure the assistant can attach generated audio to its reply without blocking text output when audio generation is slower
- Decide one implementation detail before coding:
  - whether the frontend consumes chunk events directly for live playback
  - or the backend aggregates chunks into a final playable file first
- Start with backend aggregation if live streaming creates too much frontend complexity

### Exit Criteria

- the assistant can turn a text answer into a generated audio asset
- the generated audio appears in the conversation UI with a playable result
- partial and failed TTS states are visible and recoverable

## Phase 7: Video Generation Framework And Hardening

### Objectives

- Define a stable asynchronous video generation contract without blocking earlier phases
- Finish the multimodal surface with end-to-end verification and observability improvements

### Tasks

- Add provider-neutral request and job result shapes for video generation
- Implement service-level orchestration for:
  - submit generation
  - persist job state
  - poll status
  - materialize final output into a `generated_video` asset
- Add the `generate_video` tool with a conservative first contract:
  - prompt
  - optional source asset ids
  - duration
  - aspect ratio
- Keep provider implementation pluggable so a concrete vendor can be added later without changing runtime or frontend contracts
- Add backend tests for:
  - new asset schema fields
  - audio and video ingestion
  - context assembly for audio and video
  - image generation provider mapping
  - speech generation status transitions
  - generated asset linkage to assistant messages
- Add frontend verification for:
  - audio and video rendering
  - generated asset badges
  - state transitions across queued, partial, ready, and failed
- Improve logging and observability so jobs record:
  - provider name
  - request id when available
  - job type
  - terminal error summary

### Exit Criteria

- the system has a stable contract for future video generation work
- multimodal ingestion and generation flows are covered by targeted tests
- failures are diagnosable from logs, timeline state, and job state

## Suggested Implementation Order

1. Extend persistence models, migrations, schemas, and config
2. Add capability provider interfaces and factory split
3. Implement audio transcription and video ingestion
4. Extend context assembly and runtime tool contracts
5. Extend frontend audio and video upload and playback UI
6. Formalize image generation behind the provider layer
7. Implement streaming speech synthesis with backend-first aggregation
8. Add the asynchronous video generation framework
9. Finish verification, observability, and error handling

## Files And Areas Expected To Change

- `backend/app/models/entities.py`
- `backend/app/db/session.py`
- `backend/app/schemas/assets.py`
- `backend/app/schemas/events.py`
- `backend/app/core/config.py`
- `backend/app/core/session_assets.py`
- `backend/app/providers/base.py`
- `backend/app/providers/factory.py`
- `backend/app/providers/openai_adapter.py`
- `backend/app/providers/`
- `backend/app/services/asset_service.py`
- `backend/app/services/asset_ingestion_service.py`
- `backend/app/services/context_assembler.py`
- `backend/app/services/image_generation_service.py`
- `backend/app/services/speech_generation_service.py`
- `backend/app/services/speech_recognition_service.py`
- `backend/app/services/video_understanding_service.py`
- `backend/app/services/video_generation_service.py`
- `backend/app/runtime/manager.py`
- `backend/app/mcp/registry.py`
- `backend/app/api/routes.py`
- `frontend/src/lib/api.ts`
- `frontend/src/app/App.tsx`
- `frontend/src/app/styles.css`
- `backend/tests/`

## Risks To Manage

- letting video preprocessing create too many keyframes and blow up storage, ingestion time, or prompt budgets
- coupling ASR, TTS, and video generation vendor details directly into runtime code
- making TTS feel streaming-capable in the backend while the frontend still behaves like batch playback
- allowing generated assets to bypass normal message linkage and asset lifecycle semantics
- introducing media-heavy jobs without enough status visibility for retry and debugging

## Definition Of Done

The multimodal implementation is done for the first major milestone when Jarvis can upload and persist image, audio, and video assets; transcribe audio; derive searchable video transcript and keyframe summaries; answer questions over multimodal attachments through the existing turn loop; render audio and video assets in the frontend; generate images through a formal provider abstraction; and keep speech synthesis and video generation on stable service and provider contracts for later rollout.
