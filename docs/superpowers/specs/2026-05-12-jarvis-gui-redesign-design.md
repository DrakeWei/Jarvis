# Jarvis GUI Redesign Design

Date: 2026-05-12
Status: Approved in conversation, spec written for review

## Summary

Redesign the Jarvis desktop GUI so the product feels visually close to the provided reference image: quiet, pale, rounded, reading-first, and chat-centric. The redesign should not keep the current cockpit-style wall of persistent panels. Instead, it should shift to a chat-first layout where complex agent controls exist, but are visually de-emphasized and revealed on demand.

The approved direction is:

- High-similarity visual reference to the uploaded image
- Interaction structure may change where needed
- The right-side operations area should be weak by default, not permanently visible
- Preserve the existing backend capability surface and adapt the frontend around it

## Goals

- Make the interface feel close to the approved reference image
- Make the center conversation area the dominant visual surface
- Reduce the default visual weight of `Tasks`, `Approvals`, `Logs`, `Subagents`, and `Teammates`
- Keep complex agent capabilities accessible without letting them dominate the default view
- Preserve the current runtime model and backend APIs wherever possible

## Non-Goals

- Rebuild the backend data model
- Change the agent runtime architecture
- Split the product into separate full pages for each tool area
- Turn the app into a heavy dashboard or IDE-style control surface
- Introduce dark-first styling or highly expressive marketing-page visuals

## Approved Product Direction

The approved approach is `reference-like shell + workstation core`.

This means the product should look and read like the reference UI, but still accommodate Jarvis-specific runtime features behind secondary surfaces. The default screen should feel like a focused conversation workspace. Operational complexity should appear only when the user asks for it or when the system must elevate it, such as for pending approvals.

## Information Architecture

### Left Rail

The left side becomes a narrow navigation rail visually similar to the reference image.

It contains:

- Primary global actions such as `New Session`, `Search`, `Skills`, `Plugins`, `Automations`, and `Settings`
- Project and recent session navigation
- The active session highlight
- Minimal metadata such as recent activity time

The rail is a switching surface, not an operations panel. It should stay visually thin and quiet.

### Center Conversation Surface

The center column becomes the dominant surface and should resemble the reading flow of the reference image.

It contains:

- A light title/header row for the current session or task
- A conversation timeline optimized for reading
- Inline result cards for artifacts, files, or summaries
- Inline status cards for notable runtime events
- A fixed bottom composer with a large rounded input

The center surface is the default home for interaction. It should feel coherent even when no auxiliary panel is open.

### Right Workbench Surface

The right-side capability area is no longer permanently visible.

Instead, it is exposed through a light workbench entry in the shell and opens as a drawer or slide-over panel. This panel contains advanced runtime surfaces such as:

- `Tasks`
- `Approvals`
- `Logs`
- `Subagents`
- `Teammates`

The drawer should share the same visual language as the main UI so it feels like an extension of the same product, not a separate admin panel.

## Visual Language

### Overall Tone

The UI uses a pale warm-gray palette inspired by the reference image. The base should avoid both pure white and cold blue-gray. Surfaces should rely on subtle separation, not dramatic contrast.

The desired tone is:

- Calm
- Quiet
- Desktop-like
- Reading-first
- Lightly rounded
- Structurally clear without heavy chrome

### Typography

Use a neutral sans-serif system stack. Avoid ornamental display typography. Hierarchy should come from spacing and weight more than from dramatic size jumps.

The type system should stay restrained:

- One clear title level
- One body level
- One secondary metadata level

This keeps the product feeling like a desktop tool rather than a branded landing page.

### Shape, Border, and Depth

Use a consistent rounded geometry across cards, inputs, drawers, and session items.

- Borders should be light and quiet
- Shadows should be used sparingly
- Most separation should come from surface tone and spacing

Normal cards should not depend on heavy shadow. Elevated depth is reserved for drawers, floating panels, and transient overlays.

### Message and Card Hierarchy

The center timeline should use three clear visual weights:

- `Conversation blocks` for user and assistant exchange
- `Result cards` for files, outputs, and durable artifacts
- `Status cards` for task updates, execution summaries, and approval prompts

These should not all look identical. The goal is to preserve readability while keeping runtime signal visible.

### Left Rail Styling

The rail should feel lightweight and understated.

- The active session uses a soft filled highlight
- Group labels stay visually weak
- The rail avoids panel-like heaviness

### Composer Styling

The bottom composer is one of the strongest anchors in the layout.

- Large rounded input
- Light border
- Stable bottom position
- Small secondary controls for model or run mode tucked into the edge, not promoted into a large toolbar

## Functional Mapping

### Sessions

`Sessions` remain in the left rail as the primary navigation object.

Each item should show:

- A compact title
- A recent activity hint
- A clear active state

`New Session` should appear near the top and visually match the reference pattern of starting a fresh conversation.

### Timeline and Events

The current `Lead Session` and raw timeline event list should be remapped into a conversation-like stream.

Incoming frontend data should render as:

- `Conversation message`
- `Result block`
- `Status block`

This keeps agent runtime information visible without turning the center pane into a monitoring console.

### Tasks

`Tasks` no longer occupy a permanent main-page card.

They live in the workbench drawer as a lightweight task list. If a task event is strongly relevant to the current conversation, such as creation or completion, the center timeline should show only a concise status summary.

### Approvals

`Approvals` are the main exception to the default-hidden rule.

The complete approval list lives in the workbench drawer, but any pending approval must also surface in the center timeline as a high-priority card with direct `Approve` and `Reject` actions.

This prevents hidden critical actions.

### Logs

Full execution logs move into the workbench drawer.

The center timeline should only display compact execution summaries such as:

- Read files completed
- Command finished
- Wrote file

Detailed input and output stay in the workbench.

### Subagents

`Subagents` live in the workbench drawer as operational state.

If a subagent produces a useful conclusion, that conclusion should be summarized back into the center timeline as a result block. Runtime process detail remains secondary.

### Teammates

`Teammates` also move into the workbench drawer.

The drawer may show active teammate state, recent brief, and message thread. If a teammate response materially helps the active conversation, the frontend should surface a summary into the center timeline instead of forcing the user to dig through the drawer.

### Composer Behavior

The bottom input remains the unified entry point.

Conversation, task-oriented instructions, and operational prompts all start from the same composer. Secondary controls such as model choice or run mode should remain compact and attached to the composer rather than becoming a separate heavy toolbar.

## States, Responsiveness, and Delivery Boundaries

### Offline and Error States

When the backend is unavailable, the center area should not degrade into raw technical state text. Instead, it should present a calm empty-state card that explains the backend is not connected and offers a retry or startup path. The left rail and session history remain visible.

### Empty State

A new or empty session should preserve large white space and focus. The center surface should contain:

- The product or session title
- A short explanatory line
- The bottom composer

It should not pre-fill the surface with operational panels.

### Streaming State

During generation, the UI should show growing message content and light inline runtime hints. Tool calls and background work should appear as subtle progress or status affordances attached to the relevant message rather than as separate heavy loading panels.

### Responsive Behavior

On larger desktop widths, the approved structure remains:

- Fixed left rail
- Dominant center conversation surface
- On-demand right workbench

As the window narrows:

- The workbench becomes an overlay drawer
- The left rail can collapse toward an icon-first strip
- The center reading experience remains the top priority

### Implementation Boundary

This redesign should primarily change frontend structure and presentation, not backend capability design.

Preferred implementation approach:

- Reorganize `frontend/src/app/App.tsx` around the new shell
- Rewrite `frontend/src/app/styles.css` for the approved visual language
- Reuse existing frontend API calls and backend endpoints where possible
- Add only lightweight frontend-side mapping to translate current raw event shapes into `conversation`, `result`, and `status` presentation classes

### Delivery Strategy

Implementation should proceed in two passes.

Pass 1:

- New shell layout
- Left rail redesign
- Center conversation redesign
- Bottom composer redesign
- Right workbench drawer foundation

Pass 2:

- Migrate `Tasks`, `Approvals`, `Logs`, `Subagents`, and `Teammates` into the new structure
- Add inline approval prompts
- Add compact result and status cards
- Polish empty, loading, and disconnected states

## Acceptance Criteria

- The default UI is visually close to the approved reference image
- The center conversation surface clearly dominates the layout
- The right-side operational surfaces are hidden by default and available on demand
- Pending approvals surface directly in the center timeline
- Logs no longer dominate the default screen
- Existing session, task, teammate, approval, subagent, and execution data remain accessible
- The redesign can be implemented without restructuring backend APIs
