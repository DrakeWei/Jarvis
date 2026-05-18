# Jarvis Multimodal Capabilities Design

## Goal

Fully extend Jarvis from a text-first agent into a multimodal agent that can work with text, images, audio, and video in one session model.

The design must support two things at the same time:

- multimodal understanding for uploaded session assets
- multimodal generation for assistant-created outputs

This design plans the full capability surface now, while keeping the first implementation phase focused on a stable understanding pipeline.

## Product Direction

Jarvis should use one session-native asset model for both uploaded media and generated outputs.

Planned capability surface:

- text understanding and reply
- image understanding
- audio understanding
- video understanding
- image generation and editing
- speech synthesis
- video generation

Implementation sequencing:

1. first land image, audio, and video understanding
2. then formalize image generation
3. then add speech synthesis
4. then add video generation on the same provider and job framework

## Existing Project Constraints

Jarvis already has a strong base for this work:

- session-scoped asset storage and attachment references
- ingestion jobs and derived file storage
- context assembly that expands attachments into model-ready context
- local generation support for images
- timeline-driven frontend rendering for attachment results

The design should extend those paths instead of introducing a second multimodal pipeline.

## Architecture

Jarvis should adopt an asset-centered multimodal architecture with a provider plugin layer underneath it.

### Core Layers

1. Session asset layer
   Owns the lifecycle, storage, metadata, previews, and message linkage for all uploaded and generated media.

2. Ingestion layer
   Converts raw media into derived artifacts that the runtime can search, summarize, and inject into context.

3. Capability services
   Provide business-level operations such as image generation, speech synthesis, audio transcription, and video understanding.

4. Provider plugin layer
   Hides vendor-specific transport, auth, and payload details behind capability-specific interfaces.

5. Runtime and context assembly
   Orchestrate which assets are relevant to the current turn and decide whether to inject raw model blocks or derived summaries.

### Architectural Rules

- All multimodal files must enter the system as session assets.
- Runtime code must not contain vendor-specific request logic.
- Ingestion and generation are separate responsibilities.
- Generated outputs should come back into the same session asset model as uploaded inputs.
- Context assembly must inject only the smallest useful multimodal representation for the current turn.

## Data Model

The current `session_assets`, `message_assets`, `asset_chunks`, and background job model should remain the foundation.

### Session Asset Kinds

Extend `session_assets.kind` to support:

- `image`
- `pdf`
- `docx`
- `xlsx`
- `pptx`
- `audio`
- `video`
- `generated_image`
- `generated_audio`
- `generated_video`

### New Session Asset Fields

Add:

- `origin`: `uploaded | generated | derived`
- `source_asset_id`: nullable parent asset id for outputs or derived assets
- `metadata_json`: provider-neutral structured metadata

`metadata_json` examples:

- image: width, height
- audio: duration, sample_rate, channels, language, transcript status
- video: duration, width, height, fps, has_audio, keyframe status, transcript status
- generated image: prompt, size, quality, provider
- generated audio: voice, format, provider
- generated video: prompt, duration, provider

### Asset Chunks

Reuse `asset_chunks` for all searchable derived content and extend it with time-based media fields:

- `start_ms`
- `end_ms`
- `speaker`
- `frame_index`
- `frame_timestamp_ms`

Usage:

- audio transcript chunks use `start_ms`, `end_ms`, `speaker`
- video transcript chunks use `start_ms`, `end_ms`
- video keyframe summaries use `frame_index`, `frame_timestamp_ms`
- documents continue to use page, sheet, slide, and section metadata

### Asset Visibility Rules

Store only user-visible and reusable outputs as first-class session assets:

- uploaded source files
- generated images, audio, and video
- optional video cover or poster images when they are user-visible

Keep internal derived artifacts as files plus metadata and chunks:

- extracted transcript text
- temporary audio tracks from video
- internal keyframe images used only for understanding
- generated summaries

### State Model

Extend asset lifecycle states to:

- `uploaded`
- `queued`
- `processing`
- `partial`
- `ready`
- `failed`
- `deleted`

`partial` is important for streamed or multi-stage outputs such as TTS and later video generation.

### Message Linkage

Keep `message_assets` as the only message-to-asset join model.

- user messages link uploaded assets
- assistant messages link generated assets
- internal derived artifacts do not link directly to messages

## Background Jobs

Do not create a second job system. Continue to use the existing background job and ingestion model, but make job types explicit:

- `asset_ingest_image`
- `asset_ingest_document`
- `asset_transcribe_audio`
- `asset_ingest_video`
- `generate_image`
- `generate_speech`
- `generate_video`

This keeps observability, retry policy, and timeline rendering compatible with the current runtime.

## Capability Services And Provider Interfaces

The existing text provider abstraction should remain in place for model conversation and tool calling. Multimodal generation and recognition should be introduced as parallel capability-specific provider interfaces instead of being forced into the same base adapter.

### Service Layer

Add or formalize these services:

- `image_generation_service`
- `speech_generation_service`
- `speech_recognition_service`
- `video_understanding_service`
- `video_generation_service`

Each service owns business-level request validation, asset creation, status updates, and runtime-visible outputs.

### Provider Interfaces

Add capability-specific provider interfaces:

- `ImageGenerationProvider`
- `SpeechSynthesisProvider`
- `SpeechRecognitionProvider`
- `VideoUnderstandingProvider`
- `VideoGenerationProvider`

Keep the current text model adapter as `LLMAdapter`.

### Interface Expectations

Suggested provider contracts:

- `ImageGenerationProvider.generate(request) -> GeneratedImageResult`
- `ImageGenerationProvider.edit(request) -> GeneratedImageResult`
- `SpeechSynthesisProvider.synthesize_stream(request) -> AsyncIterator[AudioChunkEvent]`
- `SpeechSynthesisProvider.synthesize_once(request) -> GeneratedSpeechResult`
- `SpeechRecognitionProvider.transcribe(request) -> TranscriptResult`
- `VideoUnderstandingProvider.summarize(request) -> VideoSummaryResult`
- `VideoGenerationProvider.generate(request) -> VideoGenerationJob | GeneratedVideoResult`
- `VideoGenerationProvider.poll(job_id) -> VideoGenerationStatus`

### Vendor Mapping

Planned first providers:

- AIDP OpenAI-compatible provider for image generation and image editing
- Volcengine speech provider for TTS streaming
- ASR provider left abstract in the interface layer, with implementation chosen later
- video generation provider left abstract in the interface layer, with implementation chosen later

### Factory Design

Replace the single provider factory pattern with capability-aware factories:

- `create_llm_adapter()`
- `create_image_generation_provider()`
- `create_speech_synthesis_provider()`
- `create_speech_recognition_provider()`
- `create_video_understanding_provider()`
- `create_video_generation_provider()`

Provider selection must come from configuration, not runtime branching.

## Runtime And Context Assembly

The runtime manager should remain the orchestrator. It should not own vendor payload mapping, transport logic, or media processing.

### Runtime Responsibilities

- accept user messages and asset references
- create turns and queue jobs
- choose whether a request needs model reasoning, asset inspection, or generation
- publish timeline events
- attach generated assets to assistant messages

### Context Assembly Rules

Extend current attachment expansion rules:

- images: inject as `input_image`
- audio: inject asset summary and relevant transcript chunks
- video: inject summary, relevant transcript chunks, and up to a small number of keyframes when visual evidence matters
- generated audio and generated video: do not auto-inject unless the user asks to analyze them

### Tool Surface

Keep existing asset inspection tools and extend only the generation side:

- `list_session_assets`
- `read_asset_summary`
- `search_asset_chunks`
- `read_asset_chunk`
- `generate_image`
- `generate_speech`
- `generate_video`

Understanding should continue to rely on generic asset tools instead of many media-specific tools unless later evidence shows the generic interface is insufficient.

## API And Frontend

### API

The current attachment APIs are already a good fit:

- `POST /sessions/{session_id}/assets`
- `GET /sessions/{session_id}/assets`
- `GET /sessions/{session_id}/assets/{asset_id}`
- `DELETE /sessions/{session_id}/assets/{asset_id}`
- `POST /sessions/{session_id}/messages`

For v1, generation should remain message-driven through tool calls. Dedicated generation endpoints can be added later only if the product introduces explicit image, speech, or video creation controls outside the conversation flow.

### Frontend

The existing composer attachment tray and timeline rendering should be extended instead of redesigned.

Required frontend upgrades:

- allow `audio/*` and `video/*` uploads
- render audio assets with duration, status, and playback controls
- render video assets with cover, duration, status, and playback controls
- show generated asset badges such as `Generated` or `TTS`
- show progressive states such as `processing`, `partial`, `failed`

### Client Data Shape

Extend frontend asset types to carry:

- `origin`
- `source_asset_id`
- `metadata_json`

This prevents frontend branching on filename or provider-specific fields.

## Configuration

Add provider-scoped configuration blocks and keep secrets out of the code path.

### LLM And Image

- `OPENAI_API_KEY`
- `OPENAI_BASE_URL`
- `OPENAI_QUERY_PARAMS_JSON`
- `OPENAI_HTTP_HEADERS_JSON`
- `JARVIS_IMAGE_PROVIDER`
- `JARVIS_IMAGE_API_KEY`
- `JARVIS_IMAGE_BASE_URL`
- `JARVIS_IMAGE_QUERY_PARAMS_JSON`
- `JARVIS_IMAGE_HTTP_HEADERS_JSON`
- `JARVIS_IMAGE_MODEL`

### Speech

- `JARVIS_SPEECH_SYNTH_PROVIDER`
- `JARVIS_TTS_API_KEY`
- `JARVIS_TTS_RESOURCE_ID`
- `JARVIS_TTS_WS_URL`
- `JARVIS_TTS_APP_ID`
- `JARVIS_TTS_ACCESS_TOKEN`
- `JARVIS_TTS_SECRET_KEY`

The V3 TTS path should be primary. The App ID, access token, and secret key fields remain reserved so later speech interfaces can use the same config surface without another migration.

## Testing

Add test coverage in four layers:

1. asset ingestion
   - audio kind detection
   - video kind detection
   - transcript and keyframe chunk persistence

2. context assembly
   - images become `input_image`
   - audio expands to transcript summaries
   - video expands to transcript summaries plus selected keyframes

3. capability services
   - image provider request mapping
   - speech synthesis stream event handling
   - generated asset creation and status transitions

4. runtime and frontend
   - assistant replies link generated asset ids
   - timeline renders audio and video assets correctly
   - composer upload flow does not regress

## Rollout Plan

### Phase 1: Multimodal Understanding

- extend asset kinds for audio and video
- add audio transcription ingestion
- add video metadata, audio extraction, keyframe extraction, and summary ingestion
- extend context assembly and asset tools
- extend frontend upload and playback support

### Phase 2: Image Generation Formalization

- keep existing image generation flow
- formalize it behind an AIDP OpenAI-compatible provider
- persist richer generation metadata in assets

### Phase 3: Streaming Speech Synthesis

- add `speech_generation_service`
- add Volcengine V3 streaming provider
- persist streamed audio into generated session assets
- expose playback in the timeline and attachment tray

### Phase 4: Video Generation

- add provider and job abstractions
- keep the API and runtime contract stable
- plug in the actual provider later without reworking the session asset model

## Risks

- video processing can explode latency and context cost if keyframe extraction is too dense
- TTS streaming has more product complexity than image generation because backend chunk handling and frontend playback must both be stable
- mixed-provider setups can make debugging harder without explicit provider, request id, and job metadata in logs
- dumping full transcripts or full video summaries into prompts will break context budgets and must be avoided

## Success Criteria

- a user can upload text, image, audio, and video assets into one session model
- Jarvis can answer questions about images directly
- Jarvis can answer questions about audio from transcript chunks without injecting full transcripts
- Jarvis can answer questions about video from transcript chunks and selected keyframes
- Jarvis can generate images into session assets
- Jarvis can synthesize assistant speech into session assets
- later video generation can plug into the same asset, job, timeline, and message-linkage model

## Recommended First Implementation Target

The first implementation target should be:

- image direct understanding
- audio transcription and retrieval
- video keyframe plus transcription understanding
- formalized image generation

Speech synthesis and video generation should be designed now, but delivered only after the understanding pipeline is stable.
