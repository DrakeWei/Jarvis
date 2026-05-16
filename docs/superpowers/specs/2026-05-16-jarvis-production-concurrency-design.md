# Jarvis Production Concurrency Design

## Goal

Define the target architecture required to evolve Jarvis from a single-machine, low-concurrency desktop agent backend into a production-grade multi-worker system that preserves stability, correctness, and observability under high concurrent load.

## Problem Statement

The current Jarvis backend is functionally rich but structurally optimized for low concurrency:

- runtime state is held in process-local memory
- background work is launched with in-process `asyncio.create_task`
- websocket fanout is backed by in-memory queues
- the database layer uses synchronous SQLAlchemy sessions and SQLite by default
- upload and ingestion pipelines are coupled directly to the API process

This architecture is acceptable for local or low-traffic usage, but it does not provide production-grade guarantees once the system needs multiple API workers, multiple background workers, or sustained concurrent sessions with attachment parsing and streaming output.

## Design Scope

This is a top-level architecture spec covering the production-grade concurrency migration. It sets the invariants, boundaries, and migration sequencing across four phases:

1. State externalization
2. Worker queue and execution separation
3. Event bus and realtime delivery redesign
4. Performance, resilience, and verification hardening

This spec does not prescribe one specific queue vendor or cloud deployment platform, but it does define the required behaviors.

## Non-Goals

- Rewriting the product into a multi-tenant SaaS in one step
- Solving billing, authentication, or permission models beyond what concurrency architecture requires
- Introducing unrelated feature changes to the end-user workflow
- Replacing the agent loop semantics unless required for concurrency correctness

## Current Failure Modes

The current implementation has several concurrency failure classes:

### Process-Local Coordination

- active turns are tracked in process memory
- pending approvals are tracked in process memory
- websocket subscriptions are tracked in process memory
- background tasks are tracked in process memory

This prevents safe horizontal scaling and makes correctness depend on a single process lifetime.

### Unbounded Realtime Fanout

Realtime session events are pushed through per-session in-memory queues without bounded backpressure semantics. Slow consumers can accumulate memory or delay fanout.

### API Process Owns Heavy Work

Turn execution, attachment ingestion, and other long-running work are started directly from the API process. Under higher load this couples request handling throughput to compute-heavy execution.

### Storage And Query Constraints

SQLite and synchronous per-request sessions are not suitable for production-grade concurrent write load, especially with turn state, tool logs, approvals, memory, and attachments all sharing the same primary store.

### Missing Global Concurrency Controls

There are no robust cross-process limits for:

- concurrent lead turns
- concurrent attachment ingestions
- per-session exclusivity
- provider request budgets
- slow websocket client handling

## Target Architecture

Jarvis should evolve into a split architecture with explicit boundaries:

- stateless API layer
- durable primary database
- distributed coordination/cache layer
- background worker layer
- durable event storage
- websocket delivery edge

Each layer must be replaceable or horizontally scalable without depending on process-local correctness.

## Core Design Principles

### Correctness Before Throughput

The system must first guarantee that only one authoritative execution path owns a turn or job at a time. Throughput optimizations come after ownership and replay semantics are made deterministic.

### Durable State, Ephemeral Compute

API workers and background workers must be disposable. Any critical state needed for turn recovery, approval recovery, attachment recovery, or event replay must survive process restarts.

### Bounded Concurrency Everywhere

No unbounded task spawning, queue growth, or upload buffering should remain in the production design. Every critical path must have explicit budgets and backpressure behavior.

### Idempotent Transitions

Every externally visible transition must be safe to retry. Duplicate delivery, worker restarts, or lease handoff must not create double execution or state corruption.

### Split Durable And Ephemeral Events

Durable timeline events should be stored for replay and user history. High-frequency transient deltas such as token streaming should use a separate bounded realtime path.

## State Model

Production concurrency requires explicit durable state ownership for:

- sessions
- turns
- approvals
- attachment ingestion jobs
- subagent or teammate jobs
- durable timeline events

The authoritative state for these entities should live in the primary database. Redis or an equivalent coordination layer may hold leases, queue metadata, short-lived stream buffers, and rate-limit counters, but not the only copy of durable business state.

### Required Durable Invariants

1. At most one worker may own active execution for a given turn at a time.
2. A session may have multiple historical turns, but only one lead turn may be active at once.
3. Approval resolution must be durable and replayable after API or worker restarts.
4. Attachment ingestion must be resumable or retryable without duplicating durable output rows.
5. Durable timeline history must remain queryable even if realtime delivery fails.

## Execution Model

### API Layer

The API layer should accept requests, validate input, persist the initial state transition, enqueue or signal work, and return quickly. It should not own long-running agent execution or heavy attachment parsing.

### Worker Layer

Background workers should execute:

- lead turn loops
- resumed turn loops
- attachment ingestion
- opportunistic background tasks such as autoname

Workers must acquire durable or coordination-backed leases before running a unit of work. Lease loss or duplicate pickup must resolve safely through idempotent state checks.

### Queueing Model

The architecture requires a real job queue with:

- visibility timeout or lease expiry
- retry with backoff
- dead-letter or failure inspection
- per-job idempotency key
- per-session sequencing guarantees where needed

The queue implementation may vary, but these semantics are required.

## Realtime Event Model

The current in-memory websocket fanout must be replaced with a multi-instance-safe event pipeline.

### Durable Events

Durable events include:

- message accepted
- message completed
- approval requested
- approval resolved
- turn started
- turn completed
- turn failed
- asset lifecycle milestones that should survive reconnect and replay

These events should be persisted in the primary database.

### Ephemeral Events

Ephemeral events include:

- token deltas
- transient runtime status updates
- short-lived progress notifications that need not survive reconnection

These should use a bounded delivery channel with explicit drop, disconnect, or downgrade behavior for slow consumers.

### Delivery Requirements

- websocket clients must reconnect with a cursor or event sequence marker
- slow consumers must not stall global event fanout
- per-session delivery should be ordered for durable events
- multi-instance API workers must be able to serve the same session stream safely

## Storage Architecture

### Primary Database

The primary durable store should move from SQLite to Postgres.

Postgres is required because the production design needs:

- concurrent write safety beyond SQLite's practical limits
- richer indexing and query planning
- transactional guarantees for turn and approval lifecycles
- better operational tooling for migrations, backup, and introspection

### Coordination Layer

Redis or an equivalent distributed coordination layer should provide:

- short-lived execution leases
- bounded stream or pubsub fanout
- queue-side counters and rate limits
- transient delivery buffers

This layer should not be the only source of truth for durable user-visible state.

### Upload And Ingestion Storage

Attachment uploads should land in durable object or filesystem-backed storage through a stream-to-disk path, not by reading entire files into memory inside the API request path.

## Concurrency Controls

The production design must explicitly bound:

- global concurrent lead turns
- global concurrent resumed turns
- global concurrent attachment ingestions
- per-session active turn ownership
- per-provider outbound request concurrency
- websocket queue depth per client
- maximum upload size, count, and parsing budget

These limits must be configurable and observable.

## Idempotency And Correctness

The following operations must be idempotent or retry-safe:

- create message and enqueue turn
- enqueue attachment ingestion
- resolve approval
- append tool execution result
- persist final assistant message
- mark turn completed or failed

The design should assume that retries, worker crashes, and duplicate deliveries are normal failure modes, not edge cases.

## Recovery Model

Recovery must be defined at the architecture level:

- API process restart must not lose active turn ownership semantics
- worker restart must not lose job ownership semantics
- reconnecting clients must be able to recover durable session history
- interrupted turns must be reconstructible from database-backed state and checkpoints
- attachment ingestion must either resume or re-run safely from durable input

## Observability

Production readiness requires first-class visibility into:

- queue depth
- worker lag
- active turn count
- attachment ingestion throughput
- websocket subscriber count
- dropped or throttled ephemeral events
- provider request latency and failure rate
- database query latency and lock contention

At minimum, the architecture must support structured logs, metrics, and request or job correlation identifiers.

## Verification Requirements

The migration is not production-grade without dedicated concurrency verification:

- load tests for concurrent sessions and streaming replies
- upload burst tests
- slow websocket consumer tests
- worker crash and retry tests
- duplicate delivery and idempotency tests
- database contention tests

Correctness under concurrency must be proven by targeted scenarios, not inferred from successful single-user behavior.

## Migration Strategy

The migration should remain staged:

1. Move durable state to Postgres and externalize runtime coordination
2. Separate API and worker execution
3. Replace in-memory realtime fanout with a multi-instance event path
4. Add bounded concurrency controls, observability, and formal verification

Each stage must preserve user-visible behavior where possible while reducing reliance on process-local correctness.

## Risks

- Half-migrated states where some code paths still rely on in-process memory
- Queue introduction without idempotent job semantics, causing duplicate execution
- Realtime redesign that improves fanout but loses ordering or replay semantics
- Postgres migration that preserves old N+1 query patterns and simply moves contention to a bigger database
- Attachment ingestion moving out of process without explicit storage ownership and cleanup contracts

## Success Criteria

The system should only be considered production-concurrency-ready when:

- multiple API workers can serve the same deployment safely
- multiple background workers can process turns and ingestions safely
- session correctness does not depend on a single Python process staying alive
- slow websocket clients cannot stall global delivery
- retries and restarts do not duplicate durable user-visible outcomes
- sustained concurrent load can be observed, throttled, and debugged with first-class signals
#+#+#+#+assistant to=functions.apply_patch code շարունակation еиҭanalysis code omitted to save tokens? Need complete patch. Use split. We keep concise but enough. Since apply_patch requires end. Let's create placeholder then update in chunks. must not parallel. We'll instead add smaller file in segments. First create placeholder. there is currently failed add? not created. We need end patch. Let's do minimal add then update. +#+#+#+#+#+ to=functions.apply_patch code ***!
