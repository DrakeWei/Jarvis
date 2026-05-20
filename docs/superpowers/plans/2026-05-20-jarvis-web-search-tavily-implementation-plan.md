# Jarvis Web Search With Tavily Implementation Plan

Date: 2026-05-20
Depends on: `docs/superpowers/specs/2026-05-20-jarvis-web-search-tavily-design.md`

## Delivery Strategy

Implement the approved Tavily-backed web search capability in five phases:

1. provider contract and config foundation
2. Tavily service and result shaping
3. local tool registration and runtime execution
4. search guardrails and answer policy enforcement
5. test coverage and smoke validation

The implementation should stay deliberately narrow in v1:

- one provider: Tavily
- one runtime-facing tool: `web_search`
- one evidence model: compressed search results with quality grading
- one control strategy: mixed triggering with platform guardrails

This phase should not expand into full browser automation, multi-provider abstraction, or a research planner.

## Phase 0: Contract Freeze

### Objectives

- Freeze the v1 scope before implementation starts
- Prevent scope drift into broader web or research features
- Make the implementation sequence unambiguous

### Tasks

- Freeze the v1 capability boundary to:
  - Tavily `search`
  - local `web_search` tool
  - evidence compression
  - evidence-quality grading
  - forced-search guardrails for high-risk current-fact prompts
  - final-answer interception when no search evidence exists
- Freeze explicit non-goals:
  - browser automation
  - page clicking or navigation
  - multi-source federation
  - new MCP server for search
  - UI citation features
- Freeze the default product behavior for weak evidence:
  - allow a best guess
  - require explicit uncertainty language

### Exit Criteria

- The team agrees the v1 scope is retrieval only, not browsing
- The team agrees weak evidence does not justify confident answers

## Phase 1: Provider Foundation

### Objectives

- Add the minimum configuration and service boundaries needed to call Tavily safely
- Keep provider-specific logic out of the agent loop

### Tasks

- Add new settings in `backend/app/core/config.py`:
  - `JARVIS_TAVILY_ENABLED`
  - `JARVIS_TAVILY_API_KEY`
  - `JARVIS_TAVILY_TIMEOUT_MS`
  - `JARVIS_TAVILY_MAX_RESULTS_DEFAULT`
- Define a small service-facing response model in a new module:
  - search query
  - search timestamp
  - compressed results
  - evidence quality
  - optional suggested answer
  - provider or error metadata
- Create `backend/app/services/tavily_search_service.py`
- Implement provider request assembly for Tavily `POST /search`
- Normalize response parsing so the runtime never depends on raw provider JSON shape
- Add conservative truncation limits for snippets and raw excerpts

### Exit Criteria

- Jarvis can call Tavily through one backend service entrypoint
- Provider-specific response details are normalized before they reach runtime code

## Phase 2: Tavily Service And Result Shaping

### Objectives

- Turn Tavily responses into bounded evidence blocks suitable for tool context
- Ensure large or messy results do not pollute the model context

### Tasks

- Implement service request parameters:
  - `query`
  - `max_results`
  - `include_domains`
  - `exclude_domains`
  - `search_depth`
  - `time_range`
  - `include_raw_content`
- Apply runtime defaults:
  - `max_results = 5`
  - `search_depth = basic`
  - `include_raw_content` only when the service can safely trim the response
- Build a compact result serializer that emits:
  - `query`
  - `searched_at`
  - `evidence_quality`
  - `results`
  - `suggested_answer`
- Restrict each result entry to:
  - `title`
  - `url`
  - `score`
  - `snippet`
  - `raw_excerpt`
- Cap returned result count to the top 3-5 items
- Truncate `raw_excerpt` aggressively so tool results remain bounded
- Implement conservative evidence grading:
  - `strong`
  - `medium`
  - `weak`
- Add one lightweight domain-hint retry rule:
  - if a domain-constrained search yields weak evidence or nothing, retry once without domain constraints

### Exit Criteria

- `web_search` evidence is compact enough to inject into model context safely
- The service can distinguish between strong, medium, and weak evidence

## Phase 3: Local Tool Registration And Runtime Execution

### Objectives

- Expose `web_search` to the agent through the existing local tool path
- Avoid introducing a new MCP server or a separate orchestration stack

### Tasks

- Add `web_search` to `backend/app/mcp/registry.py` local tool definitions
- Define a minimal JSON schema for tool input
- Decide whether `web_search` should be available in Plan Mode for v1
- Extend `RuntimeManager._execute_autonomous_tool()` to call `tavily_search_service`
- Return `ToolExecutionResult` with:
  - `completed` on successful retrieval
  - `error` on provider or config failure
  - optional structured payload if later needed for UI or metrics
- Ensure `tool_service.create_tool_execution()` records:
  - tool input
  - output summary
  - latency
  - error state

### Exit Criteria

- The lead agent can call `web_search` through the normal local tool path
- Search executions appear in the same durable tool logs as other local tools

## Phase 4: Search Guardrails And Answer Policy

### Objectives

- Prevent obvious stale answers on high-risk current-fact prompts
- Keep the decision model mixed rather than fully rules-based

### Tasks

- Add a first-pass `search_required` detector for obvious high-risk prompts:
  - `today`
  - `latest`
  - `current`
  - `now`
  - `recent`
  - `final score`
  - `stock price`
  - `weather`
  - `current CEO`
  - `news`
- Implement this detector conservatively as phrase matching in v1
- Update `_build_agent_system_prompt()` so the model knows:
  - high-risk current external facts require search
  - weak evidence requires explicit uncertainty
- Add final-answer interception:
  - if the model attempts a current-fact answer
  - and the current tool history contains no `web_search` result
  - inject a corrective follow-up requiring search
- Add answer-style guidance tied to `evidence_quality`:
  - `strong`: direct answer allowed
  - `medium`: mild caution language
  - `weak`: best guess allowed only with explicit uncertainty

### Exit Criteria

- Clearly high-risk prompts do not complete without search evidence
- Weak evidence no longer yields confident current-fact answers

## Phase 5: Tests And Smoke Validation

### Objectives

- Prove the new capability works end to end
- Catch regressions in provider access, tool wiring, and runtime policy

### Tasks

- Add service tests for `tavily_search_service`:
  - successful response parsing
  - missing API key
  - timeout or transport error
  - empty results
  - evidence-quality grading
  - snippet and raw-excerpt truncation
- Add tool-registration tests:
  - `web_search` appears in local tool definitions
- Add runtime tests:
  - `RuntimeManager` executes `web_search`
  - final-answer interception triggers when no search evidence exists
  - weak evidence path adds uncertainty requirements
- Add at least two smoke evaluation tasks:
  - "今天 NBA 东决骑士 VS 尼克斯最终比分是多少？"
  - "苹果公司现任 CEO 是谁？"
- Capture the expected outcomes for each smoke task:
  - `web_search` must be used
  - final answer must reflect current evidence
  - weak-evidence cases must contain uncertainty language

### Exit Criteria

- The new search path is covered by unit and runtime tests
- At least the two smoke tasks pass consistently enough to trust the rollout

## Suggested Implementation Order

1. Add Tavily config fields
2. Build the Tavily service wrapper and response model
3. Add result compression and evidence grading
4. Register `web_search`
5. Wire runtime execution
6. Add prompt guidance and final-answer interception
7. Add service, tool, and runtime tests
8. Add smoke evaluation tasks

## Validation Checklist

Before considering the feature ready for broader use, verify:

- search is not required for ordinary static questions
- search is required for obvious current-fact prompts
- the runtime does not inject unbounded raw search payloads
- weak evidence produces explicit uncertainty
- tool failures do not masquerade as confirmed answers

## Rollout Notes

Roll out this capability behind `JARVIS_TAVILY_ENABLED`.

During initial rollout:

- prefer enabling it in development and internal evaluation first
- inspect tool execution logs for over-triggering and weak-evidence frequency
- refine the forced-search phrase list only after observing real misses or false positives

## Risks

Primary implementation risks:

- over-triggering search and increasing latency or cost
- under-triggering on prompts the first phrase list misses
- returning too much raw content and bloating the context window
- writing guardrails that are too aggressive and create loop churn

Mitigations:

- keep the phrase list small in v1
- keep tool output bounded
- retry domain hints only once
- log search-trigger and interception behavior for debugging

## Completion Criteria

This implementation plan is complete when:

- the provider path is wired and configurable
- `web_search` is callable by the agent
- high-risk current-fact prompts cannot bypass search silently
- weak evidence is surfaced as weak, not rewritten as certainty
- smoke cases demonstrate the intended behavior end to end
