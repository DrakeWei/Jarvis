# Jarvis Web Search With Tavily Design

## Goal

Add a production-oriented web search capability to Jarvis so the agent can answer time-sensitive external questions more reliably.

The first target is not full autonomous web browsing. The target is a stable retrieval-augmented answer path for external factual questions such as:

- sports scores and outcomes
- current company leadership
- recent news and announcements
- weather, prices, and other time-sensitive facts

The first implementation should favor accuracy and stability over minimal latency or minimal cost.

## Problem

Jarvis already has a solid turn loop, structured tools, MCP support, context assembly, and durable execution state. What it lacks is a controlled way to retrieve fresh information from the public web.

Today the runtime has two failure modes for external factual questions:

1. the model answers from stale parametric memory
2. the runtime has no standard retrieval tool for current web evidence

This is especially risky for prompts such as:

- "今天 NBA 东决骑士 VS 尼克斯的最终比分是多少？"
- "苹果公司现任 CEO 是谁？"
- "OpenAI 现在主推哪个模型？"

For these prompts, a strong answer path must:

- search before answering when the question is obviously time-sensitive
- inject only compact evidence, not raw unbounded web output
- produce conservative output when evidence is weak

## Decision

Jarvis should use Tavily Search as the v1 web retrieval provider.

Tavily is a good fit for v1 because it already exposes a search endpoint designed for LLM-oriented retrieval, supports domain constraints, and returns compact result metadata that is easy to compress into tool context.

The Jarvis-facing capability should not be implemented as open-ended browser automation or a generic shell escape. It should be implemented as a narrow structured tool named `web_search`.

## Scope

This design covers:

- a local `web_search` tool exposed to the agent
- a backend Tavily service wrapper
- a mixed trigger model for when search is required
- evidence compression and evidence-quality scoring
- answer constraints for weak evidence
- runtime interception that prevents obvious stale-fact answers

## Non-Goals

This phase does not include:

- general browser navigation or page interaction
- site-specific parsers beyond optional domain hints
- a multi-step research planner
- citation rendering UX changes
- provider abstraction for multiple search vendors
- strict truth verification across multiple independent providers

## Principles

### Accuracy And Stability First

The runtime should prefer an extra search call over confidently returning a stale answer for high-risk external facts.

### Keep The Tool Surface Narrow

The model should call one structured search tool. It should not manage provider-specific details directly.

### Retrieval Must Be Controlled

Search results must be compressed into evidence blocks before they enter model context. The runtime should not inject large raw responses by default.

### Model Flexibility With Platform Guardrails

The model may decide to search on its own, but the platform must force search for clearly high-risk time-sensitive questions.

### Weak Evidence Must Stay Weak

If search results are incomplete or conflicting, Jarvis may provide a best guess, but it must explicitly state uncertainty.

## Recommended Approach

Three implementation strategies were considered:

1. model-only search decisions
2. platform-only rule-based search decisions
3. mixed decisioning with platform guardrails

The recommended approach is the mixed model.

### Why Not Model-Only

Model-only search decisions are flexible but not stable enough for the product priority. If the model fails to search on a time-sensitive prompt, it may produce a fluent stale answer.

### Why Not Platform-Only

Platform-only rules are stable but too rigid. They tend to over-search and do not handle implicit time-sensitive questions gracefully.

### Why Mixed Decisioning

The mixed approach gives Jarvis two safety layers:

- the platform forces search for clearly high-risk prompts
- the model can still decide to search for ambiguous external-fact prompts

This balances stability with flexibility and fits the current Jarvis runtime model.

## Architecture

The v1 architecture should be:

1. user message enters the runtime
2. the runtime evaluates whether the prompt clearly requires search
3. if search is clearly required, the runtime must not allow the turn to complete without a `web_search` tool result
4. if search is not clearly required, the model may still call `web_search`
5. the search service calls Tavily and compresses the response into a bounded evidence block
6. the model answers using the evidence block
7. before final output, the runtime checks whether the model is trying to answer a current external fact without search evidence
8. if so, the runtime injects a corrective follow-up that forces search

This creates three layers of protection:

- forced search for high-risk prompts
- optional search for ambiguous prompts
- final-answer interception for missed search cases

## Tool Contract

Jarvis should expose a local structured tool named `web_search`.

The model-facing input schema should remain intentionally small:

- `query`: required
- `max_results`: optional
- `include_domains`: optional
- `exclude_domains`: optional
- `search_depth`: optional
- `time_range`: optional
- `include_raw_content`: optional

The runtime should apply defaults rather than making the model manage provider-specific tuning.

### Suggested Defaults

- `max_results = 5`
- `search_depth = basic` unless the runtime or caller requests stronger retrieval
- `include_raw_content = true` only if the service can safely truncate the returned raw text

### Tool Output Shape

The tool should not expose the full Tavily response directly to the model. Instead it should return a compressed evidence block with these fields:

- `query`
- `searched_at`
- `evidence_quality`
- `results`
- `suggested_answer`

Each `results` entry should include only:

- `title`
- `url`
- `score`
- `snippet`
- `raw_excerpt`

This allows the agent loop to preserve useful web evidence while keeping context bounded.

### Example Output

```json
{
  "query": "today NBA eastern conference finals Cavaliers Knicks final score",
  "searched_at": "2026-05-20T12:34:56Z",
  "evidence_quality": "strong",
  "results": [
    {
      "title": "Knicks vs Cavaliers Box Score",
      "url": "https://www.nba.com/...",
      "score": 0.93,
      "snippet": "Final: Knicks 108, Cavaliers 101",
      "raw_excerpt": "Final ... 108-101 ..."
    }
  ],
  "suggested_answer": "Top evidence indicates the Knicks defeated the Cavaliers 108-101."
}
```

## Search Trigger Strategy

The runtime should not fully delegate search decisions to the model. It should use a mixed strategy optimized for accuracy and stability.

### Platform-Forced Search

The runtime must force search before the answer phase when the user prompt contains clear high-risk indicators such as:

- explicit recency terms: `today`, `latest`, `current`, `now`, `recent`, `just`
- explicit current-fact requests: `final score`, `stock price`, `weather`, `exchange rate`, `current CEO`, `current president`
- obvious requests for current public information: `news`, `announcement`, `official site`, `what does the website say`

The exact first version can be implemented with simple phrase-based detection. It does not need classifier complexity in v1.

### Model-Optional Search

If the prompt does not hit the forced-search rules, the model may still decide to call `web_search`.

This is necessary for ambiguous prompts such as:

- "Did the Cavaliers win that game?"
- "Who is Apple's CEO?"
- "What model is OpenAI pushing now?"

These may still require current web evidence, but platform rules do not need to overfit them in v1.

### Final-Answer Interception

Before a final answer is emitted, the runtime should inspect whether:

- the answer is framed as a current external fact
- no `web_search` result appears in the current tool history

If both are true, the runtime should block completion and inject a corrective follow-up such as:

`You are answering a time-sensitive external fact without web evidence. Call web_search before finalizing your answer.`

This prevents the most damaging silent failure: a fluent stale answer that never triggered retrieval.

## Evidence Quality

The search service should grade evidence before it is returned to the model.

The grading does not need to be perfect. It needs to be conservative and explainable.

### Strong

Use `strong` when:

- at least two high-relevance results support the same answer, or
- one clearly authoritative result directly answers the question and is strongly matched

### Medium

Use `medium` when:

- one result appears strong but not fully confirmed, or
- multiple results are relevant but not fully aligned

### Weak

Use `weak` when:

- results are only loosely relevant
- results appear stale
- the answer is inferred rather than directly supported
- the search returned too little evidence

## Answer Policy

The assistant response policy should depend on `evidence_quality`.

### Strong Evidence Response

The model may answer in a direct, confident style.

### Medium Evidence Response

The model should use modest caution, for example:

- "Based on the current search results..."
- "The available sources indicate..."

### Weak Evidence Response

The model may provide a best guess, but it must explicitly communicate uncertainty. Suitable phrasing includes:

- "I may not have the full picture yet, but the current search results suggest..."
- "This is my best estimate based on limited evidence..."
- "I cannot confirm this fully from the current search results, but the most likely answer is..."

This behavior is required because the chosen product preference is to allow best-effort answers rather than refuse outright when evidence is weak.

## Backend Integration Points

The minimum integration should touch these backend areas:

- `backend/app/core/config.py`
- `backend/app/services/tavily_search_service.py`
- `backend/app/mcp/registry.py`
- `backend/app/runtime/manager.py`

### Config

Add configuration for:

- `JARVIS_TAVILY_ENABLED`
- `JARVIS_TAVILY_API_KEY`
- `JARVIS_TAVILY_TIMEOUT_MS`
- `JARVIS_TAVILY_MAX_RESULTS_DEFAULT`

### Tavily Service

Add a new service module that:

- validates configuration
- sends Tavily `POST /search` requests
- handles API and transport failures
- truncates or omits oversized raw content
- assigns `evidence_quality`
- returns a compact response object or serialized text payload

### Tool Registration

Register `web_search` as a local tool in `backend/app/mcp/registry.py`.

This should be a local tool instead of a new MCP server in v1 because:

- it keeps the implementation smaller
- it reuses the existing local tool execution path
- it reduces operational moving parts

### Runtime Manager

Update the runtime manager in three places:

1. add `web_search` execution support in `_execute_autonomous_tool()`
2. update the agent system prompt so the model knows current external facts should be searched first
3. add final-answer interception when the model tries to answer current external facts without search evidence

Plan mode can allow `web_search` as a read-only tool if desired, but that is optional for v1.

## Domain Hints

The first version should stay simple. It does not need a full source router.

However, it should support optional domain hints so the runtime can bias searches toward more reliable sites in obvious cases. Examples:

- NBA and team-name prompts may set `include_domains = ["nba.com"]`
- company leadership prompts may set `include_domains` to an official corporate domain when clearly known

If a domain-constrained search returns weak evidence or no results, the runtime may retry once without domain constraints.

This gives Jarvis a lightweight version of source-aware behavior without needing a specialized crawling subsystem.

## Failure Handling

The search path must fail conservatively.

### Missing API Key

If the Tavily API key is missing, `web_search` should return a structured error explaining that web search is not configured.

### Timeout Or Transport Failure

If Tavily times out or fails, `web_search` should return a concise tool error. The model may still answer, but only with explicit uncertainty if it chooses to continue.

### Empty Results

If Tavily returns no useful results, the service should return `evidence_quality = weak` and an empty or near-empty result set.

### Oversized Results

If Tavily returns too much raw content, the service should truncate it aggressively. The runtime must never dump unbounded raw result payloads into model context.

## Testing Strategy

The first implementation should add tests in three layers.

### Service Tests

Add tests for the new Tavily service covering:

- successful response parsing
- missing configuration
- HTTP timeout or transport failure
- empty result handling
- evidence-quality grading
- raw-content truncation

### Tool And Runtime Tests

Add tests covering:

- `web_search` appears in local tool definitions
- `RuntimeManager` can execute `web_search`
- final-answer interception triggers when the model tries to answer a current external fact without a prior search

### Smoke Evaluation Cases

Add at least two simple evaluation cases:

- "今天 NBA 东决骑士 VS 尼克斯最终比分是多少？"
- "苹果公司现任 CEO 是谁？"

The goal of these smoke tasks is not broad benchmark coverage. The goal is to prove that the forced-search and weak-evidence behavior works end to end.

## Implementation Sequence

The recommended implementation order is:

1. add Tavily configuration
2. implement `tavily_search_service`
3. register `web_search`
4. add runtime execution support
5. add forced-search and final-answer interception rules
6. add tests

This keeps the implementation incremental and makes it easy to debug whether failures come from provider access, tool wiring, or runtime decision policy.

## Success Criteria

This design should be considered successful when all of the following are true:

- clearly time-sensitive external-fact prompts do not bypass web search
- Tavily results enter model context only as compressed evidence blocks
- weak evidence causes explicit uncertainty rather than fabricated certainty
- transport or provider failure degrades gracefully instead of pretending the answer is confirmed

## Risks

The primary risks are:

- over-triggering search and increasing cost
- under-triggering on prompts the rule set does not yet recognize
- injecting too much web text into the context window
- encouraging the model to over-trust weak evidence summaries

The chosen design addresses these risks with bounded tool output, conservative evidence grading, and final-answer interception.

## Open Questions

The initial implementation can proceed without resolving every future extension. The main post-v1 questions are:

- whether `web_search` should be available in Plan Mode by default
- whether search evidence should later surface as user-visible citations in the UI
- whether a second provider or a research-oriented mode is needed after v1 stabilizes
