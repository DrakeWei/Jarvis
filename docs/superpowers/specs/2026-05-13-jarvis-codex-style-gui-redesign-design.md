# Jarvis Codex-Style GUI Redesign Design

Date: 2026-05-13
Status: Approved in conversation, spec written for review

## Summary

Redesign the Jarvis desktop app GUI so it feels closer to Codex: minimal, quiet, precise, and premium without becoming decorative. This iteration should change only frontend presentation, visual hierarchy, and layout weighting. It must not alter backend capabilities, API contracts, or core frontend interaction logic.

The approved direction is:

- Codex-like visual tone: restrained, professional, high-end desktop tooling
- Moderate layout restructuring rather than a full information-architecture rewrite
- Light theme only for this iteration
- Narrow persistent right-side status rail with detail surfaces opening in the existing workbench drawer

## Goals

- Make the app feel like a refined desktop tool rather than a cockpit dashboard
- Shift attention decisively toward the center conversation surface
- Reduce the visual noise of operational modules without hiding critical actions
- Preserve all current runtime features and functional entry points
- Reuse the existing frontend state model and backend APIs

## Non-Goals

- Changing backend services, schemas, or runtime behavior
- Replacing the current workbench features with different product concepts
- Adding dark mode in this iteration
- Redesigning the app into a marketing-style or highly expressive visual system
- Rewriting interaction flows that already work

## Approved Constraints

The user confirmed the following constraints during design review:

- Visual style: `extremely minimal professional tool`
- Layout change level: `moderate restructure`
- Theme scope: `light theme only`
- Right-side behavior: `narrow status rail + expandable detail drawer`

In addition, the user explicitly asked that the visual result feel closer to Codex: simple without feeling cheap, polished without looking ornamental.

## Product Direction

The approved product direction is `Codex-style conversation workspace with operational sidecar`.

This means:

- The center conversation area is the dominant surface
- The shell avoids strong decoration, warm editorial styling, and dashboard-like chrome
- Advanced runtime modules remain available, but their default presence is reduced to compact signals
- The interface communicates quality through alignment, spacing, contrast discipline, and hierarchy rather than through large visual flourishes

## Information Architecture

### Left Navigation Rail

The left side remains the home for sessions and top-level navigation, but it should feel lighter and more precise than the current pane.

It contains:

- `New Session`
- Session search
- Recent sessions
- Lightweight global navigation items where already present in the UI shell

Design rules:

- Session items should read as a list, not as stacked cards
- Active state should use subtle background fill and fine border treatment
- Metadata such as timestamps should remain visible but visually weak
- The rail should support fast scanning without looking like a heavy control panel

### Center Conversation Surface

The center column becomes the clear visual anchor of the application.

It contains:

- A restrained session header
- The timeline rendered as a conversation-first reading surface
- Inline critical runtime notices where needed
- A stable bottom composer

Design rules:

- The timeline should feel like the main product, not one module among many
- Visual hierarchy should prioritize message content over surrounding controls
- Empty space should be used deliberately to reduce pressure and improve readability

### Right Status Rail

The right side remains visible, but only as a narrow status rail rather than a full third workspace column.

It contains compact signals for:

- Connection state
- Pending approvals
- Tasks
- Subagents
- Teammates
- Other high-value operational counts or state markers already supported by the current UI

Design rules:

- The rail should read as system pulse, not as a full workspace
- Each item should be compact and glanceable
- Full details should continue to open through the existing workbench drawer
- The right rail must never compete with the center conversation area for attention

### Workbench Drawer

The existing workbench drawer remains the place where advanced operational detail lives.

It continues to host:

- `Tasks`
- `Approvals`
- `Logs`
- `Subagents`
- `Teammates`

Design rules:

- It should feel like a refined secondary workspace, not a separate admin console
- Tab styling should stay compact and precise
- Dense operational data should become clearer through spacing and grouping, not bigger chrome
- The drawer should open only when the user asks for detail or must resolve a focused task

## Visual Language

### Overall Tone

The redesign should feel closer to Codex than to the current warm, editorial direction.

Desired tone:

- Minimal
- Quiet
- Precise
- Professional
- Premium
- Desktop-native rather than dashboard-heavy

Avoid:

- Warm beige or parchment-like atmosphere
- Strong glassmorphism
- Heavy shadows
- Soft rounded consumer-app styling
- Decorative gradients or brand-heavy accents

### Color System

Use a restrained light theme built from neutral cool grays and near-white surfaces.

Rules:

- The base canvas should be lightly tiered rather than flat white
- Panels should be separated mostly through value shifts and hairline borders
- Accent color should be limited to focus states, selected states, progress, and other narrow interaction cues
- Approval, warning, and runtime states should use semantic color sparingly and never dominate the screen

### Typography

Typography should feel utilitarian and polished.

Rules:

- Use a clean sans-serif stack appropriate for desktop tooling
- Keep hierarchy restrained: title, body, metadata
- Prefer quality through weight, spacing, and rhythm instead of dramatic size jumps
- Let metadata recede clearly so the message content stays dominant

### Shape, Border, and Depth

The app should look sharper and more exact than the current interface.

Rules:

- Reduce corner radii from the current softer style
- Use 1px borders as the primary separation device
- Keep shadows faint and reserve them for overlays, drawers, and selective elevation moments
- Favor flat, confident surfaces over translucent ones

### Motion

Motion should remain subtle and functional.

Rules:

- Keep hover, focus, and press feedback tight and short
- Use smooth but restrained transitions for drawer open/close and view emphasis changes
- Do not add decorative animation to otherwise static surfaces
- Preserve perceived stability during streaming and live state updates

## Component Mapping

### Session List

The current left-column session cards should be remapped into a leaner session list.

Rules:

- Keep title, recent activity, and active state
- Remove the feeling of stacked independent cards
- Make `New Session` the clearest action in the left area without over-styling it
- Preserve fast scan behavior for many sessions

### Timeline Presentation

The center timeline should continue using the current event source, but the rendering should be remapped into three presentation classes:

- `message`
- `result`
- `status`

Rules:

- User messages should be narrower and visually subordinate to assistant content
- Assistant messages should feel like the primary reading block
- Result cards should signal durable outcomes such as summaries and useful outputs
- Status cards should communicate runtime activity without turning into a monitoring console

### Composer

The composer should become the strongest stable control surface in the layout.

Rules:

- Keep the current message-send behavior
- Make the input area broader, calmer, and less panel-like
- Keep secondary controls visually tucked into the edge rather than promoted as a toolbar
- Focus state should be crisp and premium

### Right Status Rail

The right rail should summarize operational state at a glance.

Rules:

- Show concise counts, state labels, and signal markers
- Avoid long descriptions or detailed logs in the rail itself
- Use consistent compact tiles or rows
- Clicking a signal should continue to route into the corresponding workbench context

### Drawer Modules

The drawer should preserve current module capability while improving clarity.

Rules:

- `Approvals` should foreground actionable pending items
- `Logs` should separate summary rows from selected detail
- `Subagents` and `Teammates` should emphasize status and useful summaries over raw process noise
- `Tasks` should remain accessible but visually lighter than the current card treatment

## State Strategy

### Empty Session

A new session should feel intentionally open.

Rules:

- Use generous white space
- Avoid filling the center with operational cards by default
- Keep the primary cue around starting a conversation

### Streaming

Streaming should remain inside the main assistant response flow.

Rules:

- Do not split live generation into separate loading panels
- Keep runtime hints lightweight and secondary
- Preserve readability while content grows

### Offline and Waiting for Backend

When the backend is unavailable, the UI should remain calm and usable.

Rules:

- Keep navigation visible
- Replace technical-looking fallback text with a composed system state card
- Provide retry or waiting cues without making the app feel broken

### High-Priority Alerts

Pending approvals remain the primary exception to reduced operational visibility.

Rules:

- Show approval urgency in both the right status rail and the center timeline
- Use compact inline treatment rather than oversized warning surfaces
- Preserve direct approve and reject actions where they already exist

### Responsive Behavior

On wide screens, the app should read as left rail, center conversation, and narrow right rail.

As width shrinks:

- The drawer remains an overlay
- The right rail should stay compact as long as practical
- The left rail may reduce emphasis, but the conversation surface and composer must remain the priority

## Implementation Boundary

This redesign must remain frontend-first.

Expected implementation scope:

- Rework layout structure in `frontend/src/app/App.tsx`
- Replace the current visual system in `frontend/src/app/styles.css`
- Keep `frontend/src/lib/api.ts` behavior intact except for minor typing or presentation helper adjustments if needed
- Reuse the current session, timeline, approvals, logs, subagents, and teammates data flow

Not expected:

- Backend route changes
- Database changes
- Runtime orchestration changes
- Behavioral changes to existing core actions

## Acceptance Criteria

The redesign is successful when:

- The app reads as a premium minimal desktop tool closer to Codex than to the current cockpit style
- The center conversation surface is visually dominant
- The right side is reduced to a narrow status rail by default
- The workbench drawer still exposes operational detail cleanly
- Pending approvals remain visible and actionable without opening deep panels
- The frontend continues to operate on the existing backend contract
