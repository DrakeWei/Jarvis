# Jarvis GUI Redesign Implementation Plan

Date: 2026-05-12
Depends on: `docs/superpowers/specs/2026-05-12-jarvis-gui-redesign-design.md`

## Delivery Strategy

Implement the redesign as a frontend-first refactor in four phases:

1. Shell restructuring and visual foundation
2. Conversation surface remapping
3. Workbench drawer migration
4. State handling, polish, and verification

The plan intentionally preserves the current backend API surface. Most work should happen in `frontend/src/app/App.tsx` and `frontend/src/app/styles.css`, with only lightweight frontend-side data mapping added where raw runtime records need to be presented as conversation, result, or status blocks.

## Phase 1: Shell Restructuring And Visual Foundation

### Objectives

- Replace the current cockpit-style three-column wall with the approved shell
- Establish the pale, quiet visual language from the spec
- Make the center conversation surface the dominant default view

### Tasks

- Rebuild the top-level app structure in `frontend/src/app/App.tsx`
- Replace the current persistent right-side panel with a hidden-by-default workbench trigger
- Restructure the left area into a narrow navigation rail and session list
- Create a simplified top header for the active session
- Rebuild the bottom composer so it stays visually anchored and detached from the old panel styling
- Rewrite `frontend/src/app/styles.css` around the new spacing, border, radius, and color system
- Remove or replace the current dark cockpit styling and strong panel chrome

### Exit Criteria

- The default UI matches the approved high-level shell
- The left rail, center surface, and composer are visually coherent
- The right-side workbench is no longer permanently visible

## Phase 2: Conversation Surface Remapping

### Objectives

- Convert the center pane from a raw event feed into a readable conversation-first surface
- Preserve runtime signal while reducing monitoring-panel noise

### Tasks

- Add frontend presentation mapping for timeline entries
- Group current timeline records into at least three render modes:
  - `conversation`
  - `result`
  - `status`
- Design message blocks with lighter visual weight than the current event cards
- Design result cards for artifacts, documents, and durable outputs
- Design status cards for task updates, execution summaries, and approval prompts
- Replace raw tool-log presentation in the center pane with concise summaries
- Keep generated text streaming in-place inside the center conversation flow

### Exit Criteria

- The center pane reads like a conversation workspace rather than an event console
- Tool execution detail no longer dominates the default view
- Result and status blocks are visually distinct and readable

## Phase 3: Workbench Drawer Migration

### Objectives

- Move advanced operational surfaces into an on-demand drawer
- Preserve full access to existing runtime features without crowding the main view

### Tasks

- Build the right-side workbench drawer or slide-over container
- Migrate `Tasks` into the drawer as a compact list with lightweight creation controls
- Migrate `Approvals` into the drawer while also exposing pending approvals inline in the center pane
- Migrate `Logs` into the drawer with selectable detail view
- Migrate `Subagents` into the drawer with status and summary emphasis
- Migrate `Teammates` into the drawer with active thread support
- Add open/close state handling so the drawer behaves correctly across session switches

### Exit Criteria

- `Tasks`, `Approvals`, `Logs`, `Subagents`, and `Teammates` are accessible through the drawer
- Pending approvals are still visible without opening the drawer
- The main screen remains visually close to the approved reference

## Phase 4: State Handling, Polish, And Verification

### Objectives

- Make the redesigned UI robust across empty, loading, offline, and narrow-width states
- Verify the redesign still exposes the core product capabilities

### Tasks

- Replace raw backend boot and stream status text with approved empty or offline state cards
- Add a clean empty session state with large white space and a focused composer
- Add lightweight inline progress styling for streaming and background activity
- Implement responsive behavior for narrow widths:
  - workbench becomes overlay drawer
  - left rail collapses toward icon-first presentation where necessary
- Verify active session persistence and switching still work after the structural refactor
- Verify drawer interactions do not break approval, task, subagent, or teammate actions
- Add or update frontend tests for shell rendering and critical interaction states
- Run the relevant frontend verification path available in the repo

### Exit Criteria

- Empty, offline, and active states all render in the new design language
- The layout remains usable on reduced window widths
- Core interactive actions still function after the redesign

## Suggested Implementation Order

1. Replace the old shell and install the new layout primitives
2. Restyle the application and composer to match the approved visual direction
3. Add timeline presentation mapping and new center-surface card types
4. Move operational modules into the workbench drawer
5. Add inline approval prompts and compact execution summaries
6. Polish empty, loading, offline, and responsive states
7. Verify interaction regressions and update tests

## Files And Areas Expected To Change

- `frontend/src/app/App.tsx`
- `frontend/src/app/styles.css`
- `frontend/src/lib/api.ts` only if small frontend typing or helper adjustments are needed
- `frontend/src/` additional component or helper files if the refactor benefits from extracting shell or card subcomponents

## Risks To Manage

- Overfitting the visual shell to the reference image and losing access to critical runtime controls
- Letting approval visibility regress when the right-side surfaces become hidden by default
- Keeping the center timeline readable while still surfacing enough structured runtime signal
- Allowing the refactor to sprawl into backend API changes instead of staying frontend-first
- Breaking existing session-switching and live-update flows during layout migration

## Definition Of Done

The redesign is done when the app opens into the approved pale, reference-like shell, the center conversation surface dominates the experience, advanced runtime modules are available through an on-demand workbench drawer, pending approvals surface inline, and the existing backend-powered capabilities remain usable without relying on the old cockpit layout.
