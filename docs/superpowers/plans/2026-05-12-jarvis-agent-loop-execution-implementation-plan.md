# Jarvis Agent Loop Execution Implementation Plan

Date: 2026-05-12
Depends on: `docs/superpowers/specs/2026-05-12-jarvis-agent-loop-execution-design.md`

## Delivery Strategy

Implement the agent-loop upgrade in four phases:

1. Tool-message and workspace plumbing
2. Minimal agent loop in the backend runtime
3. Inline approval UX above the composer
4. Verification with real task-style prompts

The goal is to preserve the current GUI and session model while replacing the "explain instead of do" behavior for ordinary work requests.

## Phase 1: Tool Message And Workspace Plumbing

### Objectives

- Make the provider layer capable of round-tripping tool-use and tool-result messages
- Allow request-scoped workspace resolution outside the current Jarvis project

### Tasks

- Extend the OpenAI-compatible adapter message conversion to support:
  - assistant tool calls
  - user tool results
- Add request-scoped workspace resolution for:
  - explicit absolute paths
  - explicit local project names
- Refactor tool execution so `list_files`, `read_file`, `write_file`, `edit_file`, and `bash` can run against a resolved target workspace instead of only `settings.project_root`

### Exit Criteria

- Provider requests can carry tool-use state through multiple loop iterations
- A named local project can be resolved to a concrete workspace path for one request

## Phase 2: Minimal Agent Loop

### Objectives

- Replace prose-only task handling with a bounded tool-use loop
- Preserve pure chat behavior for non-task requests

### Tasks

- Add task-intent classification to distinguish:
  - question-answer turns
  - do-work turns
- For do-work turns, run a bounded loop:
  - build messages
  - provide tool schemas
  - execute safe tool calls
  - append tool results
  - continue until final answer
- Keep `list_files`, `read_file`, `write_file`, and `edit_file` autonomous by default
- Keep `bash` on the approval path

### Exit Criteria

- A request to create a file in a specified local project results in an actual file write
- Pure explanatory prompts still return direct chat responses

## Phase 3: Inline Approval UX

### Objectives

- Move active approval interaction to the composer area
- Keep the existing approval store and decision API

### Tasks

- Render a compact approval bar directly above the composer
- Show:
  - explanation
  - action preview
  - `Allow` / `Reject`
- Resume or reject the paused action without requiring the user to type yes/no
- Keep approval counts and history available elsewhere in the UI

### Exit Criteria

- Pending approvals are actionable without typed follow-up messages

## Phase 4: Verification

### Objectives

- Verify the agent behaves like a doer rather than a code explainer

### Tasks

- Verify direct chat prompts still answer normally
- Verify a task such as:
  - `在 learn-claude-code 项目里新增一个简易的 python 爬虫脚本`
  creates a file instead of only returning code
- Verify explicit command path still works
- Verify frontend build and backend syntax checks pass

### Exit Criteria

- Task prompts produce concrete workspace changes
- Approval UX and session flow remain functional
