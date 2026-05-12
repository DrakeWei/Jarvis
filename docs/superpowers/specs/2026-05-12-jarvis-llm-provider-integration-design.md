# Jarvis LLM Provider Integration Design

Date: 2026-05-12
Status: Approved in conversation, spec written for review

## Summary

Integrate a real OpenAI-compatible LLM provider into the Jarvis backend so ordinary natural-language user messages are answered by an actual model instead of a rule-based fallback. Reuse the adapter direction from `learn-claude-code`, but scope the first version to a single OpenAI-compatible provider rather than importing the entire agent runtime.

The first version should preserve the current frontend protocol and the current explicit tool-command workflow. The backend should use real LLM text generation for ordinary chat, while still routing explicit commands such as `read README.md` or `bash: pwd` through the existing rule-based tool path.

## Goals

- Replace the current placeholder natural-language fallback with a real LLM response path
- Reuse the `learn-claude-code` provider architecture direction where it materially reduces risk
- Keep the current frontend API unchanged
- Preserve the current explicit tool-command behavior
- Support OpenAI-compatible endpoints through environment variables
- Fail with clear user-visible errors when provider configuration or requests fail

## Non-Goals

- Multi-provider support in this phase
- Model-controlled tool calling
- A full agent loop that lets the model decide when to use tools
- Approval integration for LLM-selected tools
- Conversation compaction, retrieval, or memory systems
- Frontend protocol redesign

## Approved Approach

Use a thin provider layer inspired by `learn-claude-code`, but scoped to OpenAI-compatible text generation only.

The backend runtime should keep its current explicit-command path for tool execution. The new provider path should only handle ordinary natural-language messages. This keeps the change set bounded while immediately solving the current user-visible problem: normal messages produce scaffold text instead of an actual reply.

## Provider Module Structure

Add a thin provider package under `backend/app/providers/`.

Recommended files:

- `base.py`
  Shared response and block types, such as `TextBlock` and `LLMResponse`
- `openai_adapter.py`
  OpenAI-compatible HTTP request implementation
- `factory.py`
  `create_client()` entrypoint that returns the configured provider client
- `__init__.py`
  Public exports for the runtime layer

The runtime should depend on the provider package through a stable interface, not through raw HTTP details.

## Configuration

### Required Environment Variables

- `OPENAI_API_KEY`
- `MODEL_ID`

### Optional Environment Variables

- `OPENAI_BASE_URL`
- `OPENAI_WIRE_API`

### Default Behavior

- `OPENAI_BASE_URL` defaults to the official OpenAI base URL
- `OPENAI_WIRE_API` defaults to `chat_completions`
- Missing `MODEL_ID` should produce a clear configuration error instead of a silent fallback

Configuration errors should be surfaced to the user as explicit assistant-visible failures rather than hidden behind scaffold text.

## Runtime Integration Strategy

The runtime should use a two-path decision inside `_run_lead_turn()`.

### Path 1: Explicit Tool Commands

If the user message is clearly an explicit tool instruction, keep using the current rule-based tool path.

Examples:

- `read README.md`
- `bash: pwd`
- `write path/to/file.txt`
- `edit path/to/file.txt`

These requests should continue through `_plan_steps()` and `_execute_steps()` so that existing approvals, execution logging, and tool summaries remain intact.

### Path 2: Natural-Language Messages

If the user message is not an explicit tool instruction, send it to the provider layer and return the generated text as the assistant reply.

Examples:

- `Who are you?`
- `这个项目怎么启动？`
- `Summarize what this app does`

This path should not use scaffold fallback text. It should either return model output or return a clear provider error.

## Initial Provider Scope

The first version should support text generation only.

Do not implement:

- model-driven tool selection
- tool-call parsing
- approval handling for model-selected tools
- multi-turn tool loops

The goal of this phase is to make ordinary chat messages behave like real chat while keeping the current explicit-command workflow stable.

## Error Handling

Provider failures should be converted into readable assistant-visible responses.

Minimum categories:

- Configuration error
  - missing `OPENAI_API_KEY`
  - missing `MODEL_ID`
- Request error
  - authentication failure
  - invalid base URL
  - network failure
  - upstream error response
- Response error
  - empty output
  - malformed payload

These failures should not fall back to the old scaffold sentence.

## Logging And Persistence Boundaries

This phase should not redesign persistence around LLM request logging.

Required behavior:

- user-visible provider failures must appear in the session timeline as assistant replies
- existing tool execution persistence must continue to work for explicit tool commands

Deferred behavior:

- dedicated database records for provider request and response metadata
- token accounting
- full audit logging for LLM traffic

## Acceptance Criteria

- Natural-language messages are answered through the configured OpenAI-compatible provider
- Explicit tool commands still use the current rule-based tool path
- Missing provider configuration produces a clear visible error
- Provider request failures produce a clear visible error
- The frontend does not require API changes to use the new backend behavior
- The change is implemented without importing the entire `learn-claude-code` runtime

## Risks To Manage

- Letting provider integration sprawl into a full runtime rewrite
- Breaking explicit tool-command behavior while adding LLM chat
- Hiding provider configuration or request failures behind misleading fallback text
- Coupling runtime logic directly to raw OpenAI HTTP details instead of a provider boundary
