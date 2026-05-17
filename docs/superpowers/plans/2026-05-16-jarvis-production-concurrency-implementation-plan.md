# Jarvis Production Concurrency Implementation Plan

Date: 2026-05-16
Depends on: `docs/superpowers/specs/2026-05-16-jarvis-production-concurrency-design.md`

## Delivery Strategy

Implement the concurrency architecture in four phases:

1. State externalization and storage foundation
2. Worker queue and execution separation
3. Realtime event bus and websocket redesign
4. Performance, resilience, and verification hardening

The immediate execution focus is Phase 1 because the current system's biggest structural risk is correctness depending on a single Python process.

## Phase 1: State Externalization And Storage Foundation

### Objectives

- Move the primary durable store from SQLite assumptions toward Postgres readiness
- Remove reliance on process-local state for critical execution ownership
- Introduce explicit durable or coordination-backed models for turn and job ownership

### Tasks

- Add production-ready database configuration paths for Postgres
- Audit and adapt persistence code so it does not assume SQLite behavior
- Introduce durable state for:
  - active turn ownership
  - approval recovery ownership
  - attachment ingestion jobs
  - background job tracking
- Introduce a coordination layer abstraction for:
  - short-lived execution leases
  - bounded counters
  - transient coordination metadata
- Refactor restart recovery to rebuild state from database and coordination storage instead of relying on a single in-memory singleton
- Define explicit state transitions and idempotency keys for turn and ingestion jobs

### Exit Criteria

- Critical execution ownership is no longer authoritative in process-local memory
- The system can be restarted without losing correctness of active-turn and approval ownership
- Primary state is structured to run on Postgres safely

## Phase 2: Worker Queue And Execution Separation

### Objectives

- Remove long-running execution from the API process
- Introduce a real queue-backed worker model with retry and lease semantics

### Tasks

- Separate API responsibilities from execution responsibilities
- Introduce worker queues for:
  - lead turn execution
  - resumed turn execution
  - attachment ingestion
  - lightweight background tasks such as autoname
- Add retry policy with backoff and failure classification
- Add per-session sequencing rules so only one lead turn is active at once
- Add dead-letter or failure inspection support for permanently failed jobs
- Refactor API endpoints so they enqueue work and return quickly instead of owning heavy execution

### Exit Criteria

- API workers are no longer responsible for running long-lived turns or ingestion directly
- Background jobs can be retried or recovered without duplicating user-visible outcomes
- Worker concurrency can be limited independently of API concurrency

## Phase 3: Realtime Event Bus And Websocket Redesign

### Objectives

- Replace in-memory websocket fanout with a multi-instance-safe event pipeline
- Separate durable timeline events from transient token streams

### Tasks

- Introduce an external event bus or bounded stream layer for realtime delivery
- Define event sequencing and cursor semantics for reconnecting clients
- Keep durable session timeline events persisted in the primary database
- Move ephemeral token deltas and transient runtime signals into a bounded delivery path
- Add slow-consumer handling:
  - bounded per-client queue
  - disconnect or downgrade policy
  - explicit metric for dropped transient events
- Refactor websocket handlers so multiple API workers can serve the same session safely

### Exit Criteria

- Realtime delivery no longer depends on process-local subscriber lists
- Slow websocket consumers cannot stall global delivery
- Clients can reconnect and recover durable history with ordered cursors

## Phase 4: Performance, Resilience, And Verification Hardening

### Objectives

- Bound resource usage under sustained load
- Make concurrency behavior observable and testable
- Remove obvious throughput killers that would survive the architecture migration

### Tasks

- Add bounded concurrency controls for:
  - global lead turns
  - resumed turns
  - attachment ingestions
  - provider request concurrency
  - websocket queue depth
- Replace whole-file in-memory upload buffering with stream-to-disk upload paths
- Eliminate high-impact N+1 query patterns on:
  - session list
  - message reconstruction
  - asset lookup
  - timeline refresh paths
- Add structured logging, metrics, and correlation identifiers across API and worker layers
- Add verification coverage for:
  - multi-session load
  - upload bursts
  - worker crash and retry behavior
  - duplicate delivery and idempotency
  - slow websocket clients
  - database contention scenarios

### Exit Criteria

- The system exposes actionable concurrency signals during load
- Resource limits are explicit and enforceable
- Concurrency behavior is covered by targeted verification rather than ad hoc manual testing

## Sequencing Notes

- Phase 1 must land before any serious horizontal scaling attempt because correctness currently depends on in-process state
- Phase 2 should begin once durable ownership and idempotency boundaries are stable enough to support queue-driven execution
- Phase 3 should follow Phase 2 because event fanout redesign depends on execution already being detached from API workers
- Phase 4 should run partially in parallel where safe, but full performance and resilience hardening only makes sense after the first three phases define the real bottlenecks

## Risks To Watch During Implementation

- Leaving hidden process-local ownership paths alive during Phase 1
- Introducing a job queue before turn and ingestion operations are idempotent
- Preserving old synchronous query shapes and simply moving contention into Postgres
- Treating realtime token streaming as durable business state instead of a bounded transient channel
- Migrating uploads or ingestion out of process without explicit cleanup and replay contracts

## Recommended First Execution Slice

Start with Phase 1 on a narrow but critical surface:

1. Make the primary database Postgres-ready
2. Externalize active turn ownership and approval recovery ownership
3. Add durable ingestion job state
4. Define the first lease and idempotency contracts

This slice reduces the highest-risk correctness failures before the system takes on queue and event bus complexity.
