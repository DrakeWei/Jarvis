# Jarvis Session Attachments Design

## Goal

Add session-scoped attachments so a user can upload images, PDFs, and Office documents in a conversation, then ask Jarvis to reason over them without copying files into the workspace.

## Strategy

- Images: store locally, then pass to multimodal-capable models as image inputs.
- PDF and Office: store locally, extract structured text and chunks, then inject only relevant summaries and chunks into model context.
- Attachments are session assets first, not pasted into message text.

## v1 Scope

In scope:

- Upload images, PDF, DOCX, XLSX, and PPTX in a session.
- Attach one or more assets to a user message.
- Persist raw files under session-owned storage outside the workspace tree.
- Run async ingestion for metadata, previews, text extraction, and chunk generation.
- Support direct image input plus document chunk retrieval.
- Show attachment status in the composer and timeline.
- Preserve asset references in replay and checkpoints.

Out of scope:

- OCR enhancement for scanned documents.
- Cross-session asset sharing.
- External object storage.
- Fine-grained asset ACLs.
- Rich document annotation and full layout reconstruction.

## Data Model

Add three tables:

- `session_assets`: asset metadata, lifecycle, storage paths, hash, size, type, errors.
- `message_assets`: join table from a message to one or more assets.
- `asset_chunks`: parsed document chunks with location metadata such as page, sheet, slide, and section path.

Raw files live under a session-owned data directory. Derived artifacts such as previews and extracted text live beside the original file. The database stores metadata and paths, not binary blobs.

## API

Add:

- `POST /sessions/{session_id}/assets`
- `GET /sessions/{session_id}/assets`
- `GET /sessions/{session_id}/assets/{asset_id}`
- `DELETE /sessions/{session_id}/assets/{asset_id}`

Extend `POST /sessions/{session_id}/messages` to accept `asset_ids` in addition to text content.

## Runtime Design

The runtime should treat a user message as text plus asset references. Context assembly should include:

- user text
- ready image inputs
- short summaries of attached documents
- retrieved document chunks relevant to the current question

The runtime should never inject an entire large PDF or Office document into a single prompt by default.

## Frontend Design

Upgrade the composer from a plain textarea to a text-plus-attachment surface:

- file picker
- drag and drop
- attachment tray
- per-asset status
- remove before send

Images can show thumbnails. PDF and Office files should show compact file cards.

## Ingestion Pipeline

Upload returns quickly, then a background pipeline does validation and derivation:

- images: metadata and thumbnail generation
- PDF: text extraction, page indexing, chunking
- DOCX: paragraph, heading, and table extraction
- XLSX: sheet inventory, header sampling, table-like ranges
- PPTX: slide text and notes extraction

Assets move through `uploaded -> processing -> ready | failed`.

## Tools

Add session asset tools so the model can inspect parsed content without prompt bloat:

- `list_session_assets`
- `read_asset_summary`
- `search_asset_chunks`
- `read_asset_chunk`

## Constraints

- Uploaded files do not enter the project workspace by default.
- Checkpoints store asset IDs and selected chunk references, not file bytes.
- Frontend and backend both enforce MIME and size validation.
- Timeline events should summarize attachment lifecycle changes instead of dumping extracted content.

## Implementation Order

1. Data model and storage layout
2. Asset upload API
3. Composer attachment tray
4. Background ingestion worker
5. Runtime and context assembly integration
6. Provider image input support
7. Asset retrieval tools
8. Timeline, replay, and checkpoint integration

## Risks

- Unbounded prompt growth if document injection bypasses retrieval.
- Parsing variability across Office formats.
- Confusion between session-owned assets and workspace files if storage boundaries are unclear.

## Success Criteria

- A user can attach at least one image and one document in the same session.
- Jarvis can answer questions about the image directly.
- Jarvis can answer questions about the document from retrieved chunks without inlining the full file.
- Removing a session leaves no orphaned user-visible asset records.
