# Jarvis Agent Loop Execution Design

Date: 2026-05-12
Status: Approved in conversation, spec written for review

## Summary

Upgrade the Jarvis backend from a two-path runtime into a minimal task-executing agent loop.

Today the system behaves in two separate modes:

- explicit tool commands such as `read README.md` or `bash: pwd`
- ordinary natural-language chat through the configured LLM provider

That is sufficient for question answering, but not for autonomous project work. Requests such as "add a simple Python crawler script in the learn-claude-code project" are currently answered with code or suggestions instead of being executed.

The new design should let Jarvis treat "do work" requests as agent tasks. The model should be able to inspect files, choose a destination, create or edit files, and then report what it changed. High-risk actions such as `bash` remain approval-gated.

This design also upgrades approvals so the current pending approval appears directly above the composer as an inline decision bar with `Allow` and `Reject`, instead of forcing the user to type a follow-up yes/no response.

## Goals

- Let Jarvis execute ordinary coding tasks instead of only explaining how to do them
- Preserve normal chat behavior for pure question-answer interactions
- Preserve explicit command behavior for users who want direct control
- Allow safe autonomous use of read/list/write/edit tools inside the current workspace
- Allow temporary workspace switching when the user explicitly names another local project
- Keep `bash` behind explicit approval
- Surface pending approvals as inline UI actions above the composer

## Non-Goals

- Full import of the `learn-claude-code` `s_full.py` runtime
- Multi-agent orchestration redesign in this phase
- Project deletion tools or destructive filesystem expansion
- Arbitrary cross-filesystem autonomy without user intent
- Replacing the current frontend protocol wholesale

## Approved Approach

Implement a minimal agent loop inside the existing Jarvis runtime instead of directly importing the full `learn-claude-code` harness.

This keeps the current GUI, session storage, approval system, and event model intact, while upgrading the lead turn behavior from:

- answer directly
- or execute explicit commands

to:

- detect whether the user is asking a question or asking the agent to do work
- for work requests, run a bounded tool-use loop where the model decides which safe tools to use
- stop when the model returns a final answer

## Runtime Model

### Chat Path

For requests that are clearly conversational or explanatory, Jarvis continues to answer directly through the configured LLM provider.

Examples:

- `什么是 Transformer 架构？`
- `你是谁？`
- `QKV 矩阵是什么？`

### Agent Task Path

For requests that are clearly asking Jarvis to perform work, the runtime should enter an agent loop instead of just returning prose.

Examples:

- `在 learn-claude-code 项目里新增一个简易的 python 爬虫脚本`
- `帮我在这个项目里加一个 README 使用说明`
- `在目标项目里新增一个工具文件并接到现有结构中`

The loop should continue until the model stops requesting tools or the runtime hits a bounded iteration limit.

## Workspace Selection Rules

### Default Workspace

If the user does not specify another project, all tool actions are rooted in the current Jarvis project workspace.

### Explicit Target Project

If the user explicitly names another project or an absolute local path, the runtime may temporarily retarget the tool workspace to that project for the current request.

Supported resolution modes for this phase:

- explicit absolute filesystem path
- explicit local project name that can be resolved to a known local project directory

If the target cannot be resolved confidently, the runtime should stop and ask for a more specific path instead of guessing.

### Request-Scoped Switching

Workspace switching is request-scoped, not global. A task aimed at another project does not permanently change the active Jarvis workspace.

## Tool Boundaries

### Default Allowed Tools

The minimal agent loop may autonomously use:

- `list_files`
- `read_file`
- `write_file`
- `edit_file`

These are the core safe project-work tools for inspection and file creation or modification.

### Approval-Gated Tool

`bash` remains approval-gated.

The model may request it, but the runtime must pause for approval before executing.

### Deferred Tools

This phase does not add autonomous delete or move operations, nor arbitrary project-external writes.

## Agent Loop Shape

The loop should follow the same basic harness pattern used in `learn-claude-code`, but inside Jarvis' existing runtime boundary.

Recommended flow:

1. Build the current message history
2. Add a system prompt that defines the agent behavior and current workspace
3. Provide the tool list to the model
4. Let the model return either plain text or tool calls
5. Execute safe tool calls
6. Append tool results back into the loop context
7. Continue until the model returns a final answer

The key upgrade is that "do work" requests become tool-using loops instead of ordinary text answers.

## Approval UX

Pending approvals should no longer rely on the user sending a follow-up yes or no message.

### Inline Approval Bar

When an approval is requested, the frontend should render a compact approval bar directly above the composer.

It contains:

- a natural-language explanation of the requested action
- a compact preview of the command or action summary
- `Allow` and `Reject` buttons

### Relationship To The Right Rail

The right-side status area may still show approval counts and history, but the active decision surface for the current approval should be the inline approval bar above the composer.

### Resume Behavior

When the user clicks `Allow`, the paused agent loop continues.

When the user clicks `Reject`, the loop resumes with the rejection outcome and the agent must decide what to do next without requiring a typed yes/no turn.

## Execution Expectations

For a request such as:

`在 learn-claude-code 项目里新增一个简易的 python 爬虫脚本`

The expected path is:

1. Resolve `learn-claude-code` as the target project
2. List files in that project
3. Inspect a minimal amount of structure if needed
4. Choose a suitable destination such as `scripts/simple_crawler.py`
5. Write the file directly
6. Return a completion message that reports the path and what was added

Returning only a code sample or asking the user for confirmation on a safe write is not the desired behavior for this class of request.

## Acceptance Criteria

- Pure question-answer requests still behave like normal chat
- Work requests enter a tool-using agent loop
- Safe file creation and editing happen automatically inside the allowed workspace
- Explicitly named external local projects can be targeted for a single request
- `bash` continues to require approval
- Active approvals are actionable through inline UI buttons above the composer
- A request to add a file in another explicitly named local project results in the file being created, not just explained

## Risks To Manage

- Over-classifying question-answer prompts as work requests
- Under-classifying real task requests as pure chat
- Letting workspace resolution guess incorrectly
- Expanding autonomy beyond the intended safe tool set
- Breaking existing session, approval, or timeline behavior while introducing the loop
