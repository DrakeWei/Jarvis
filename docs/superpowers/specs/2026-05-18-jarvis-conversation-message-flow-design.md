# Jarvis Conversation Message Flow Redesign Design

Date: 2026-05-18
Status: Approved in conversation, spec written for review

## Summary

Redesign the center conversation timeline so it feels premium, reading-first, and structurally clear instead of looking like a stack of generic rounded chat cards. This iteration changes only the frontend presentation of the center conversation flow. It must not alter backend APIs, session state behavior, or the surrounding shell layout.

Approved direction:

- User messages remain right-aligned but become simple single-surface message bubbles
- Assistant replies stop using a response card and instead render as `logo + Jarvis` header plus unboxed body content
- Runtime and tool events leave the main message hierarchy and become weak collapsed status rows by default
- Streaming and execution states use a lightweight `Jarvis · Working` header with subtle animated dots

## Goals

- Remove the low-end feel caused by repeated rounded response cards and redundant labels such as `INSTRUCTION`, `RESPONSE`, and `You`
- Make Jarvis replies read like a premium conversation transcript rather than boxed chat output
- Preserve clear distinction between user input, assistant output, and system activity without relying on heavy chrome
- Reduce noise from tool activity and runtime updates by collapsing them unless the user asks for detail
- Keep the implementation tightly scoped to the center conversation experience

## Non-Goals

- Redesigning the left rail, right status rail, workbench drawer, or bottom composer layout
- Changing backend event types, API schemas, or timeline payloads
- Reworking approval logic, task logic, or tool execution semantics
- Adding a new visual theme for the entire application

## Current State

The current center timeline in [App.tsx](/Users/bytedance/Desktop/python/Jarvis/frontend/src/app/App.tsx) maps most message and runtime events into the same card-oriented presentation pattern. In [styles.css](/Users/bytedance/Desktop/python/Jarvis/frontend/src/app/styles.css), both user and assistant messages share the `timeline-card` surface with rounded corners, borders, and filled backgrounds.

Observed problems:

- User messages show extra framing metadata such as `Instruction` and `You`
- Assistant replies look like large generic response boxes rather than authored output
- Tool activity and runtime notices still compete with conversation content in the same visual language
- The bubble outline and fill treatment contributes to a cheaper appearance because the surfaces do not feel materially unified

These are presentation problems. The current event model, grouping logic, and streaming wiring should be reused.

## Approved Constraints

The user confirmed the following constraints during design review:

- User message placement: `right-aligned`
- User message metadata: `no You label, no INSTRUCTION label`
- Assistant message framing: `every complete reply keeps a fixed logo + Jarvis title row`
- Assistant surface: `no enclosing response box`
- Runtime state label: `Jarvis · Working`
- Tool and system events: `collapsed by default`

## Design

### Message Hierarchy

The center timeline should use three visual layers.

1. User messages.
   Right-aligned, single-surface warm-gray bubble, no label, no badge, no explicit timestamp in the bubble.
2. Assistant messages.
   Fixed `logo + Jarvis` title row for each complete reply, then unboxed markdown body on the main reading axis.
3. System events.
   Default-collapsed weak status rows for tool execution, approvals, and runtime notices. They must read as supporting traces, not as main conversation blocks.

### User Message Rules

- Keep the message bubble compact and quiet
- Use one color family for fill and edge treatment
- Remove the current badge-style framing and author label
- Preserve right alignment so user input still anchors the thread

### Assistant Message Rules

- Every complete reply gets a persistent `logo + Jarvis` header
- The body is not wrapped in a card, panel, or filled response box
- Markdown content should expand directly below the header as the dominant reading surface
- Streaming output uses the same structure instead of a separate placeholder card

### Runtime and Working State

- During streaming or task execution, the assistant header changes to `logo + Jarvis · Working`
- The `Working` state includes a subtle three-dot breathing animation rather than a spinner
- The header returns to plain `logo + Jarvis` once the reply is complete
- No extra thinking card or skeleton block should appear in the message flow

### System Event Rules

- Keep the current grouping behavior for consecutive tool executions
- Render grouped executions and single status items as low-contrast collapsed rows by default
- Expanded details can show timestamps and per-event summaries
- System rows must remain visibly subordinate to user and assistant messages

## Implementation Approach

Implementation should stay inside [App.tsx](/Users/bytedance/Desktop/python/Jarvis/frontend/src/app/App.tsx) and [styles.css](/Users/bytedance/Desktop/python/Jarvis/frontend/src/app/styles.css).

Planned changes:

- Split message rendering into distinct user, assistant, and status structures instead of routing all message-like content through the same card shell
- Remove `Instruction`, `Response`, and `You` framing from the main timeline presentation
- Add a dedicated assistant header structure for `logo + Jarvis` and `logo + Jarvis · Working`
- Preserve the existing `tool-group` data grouping but restyle it as a weak collapsed trace row
- Keep the scope limited to the center conversation pane without changing the rest of the shell layout

## Acceptance Criteria

- User messages render as right-aligned bubbles with no author label or instruction badge
- Assistant replies render with a fixed Jarvis header and no enclosing response box
- Streaming replies display `Jarvis · Working` with lightweight animated dots
- Tool and runtime events appear collapsed by default and do not visually dominate the conversation
- The new user bubble treatment no longer relies on mismatched border and fill colors

## Risks and Mitigations

- Removing the assistant card could make long replies feel visually loose.
  Mitigation: keep a strong assistant header, controlled line length, and disciplined spacing.
- Demoting system events too far could hide important runtime signals.
  Mitigation: preserve grouped summaries and keep approval-related traces visible as collapsed but legible rows.
- Mixing new assistant structure with existing generic timeline markup could create styling conflicts.
  Mitigation: introduce explicit class splits rather than only overriding shared `timeline-card` rules.
