# Jarvis Eval Trials And Stronger Graders Design

Date: 2026-05-21

## Goal

Strengthen the Jarvis eval harness in two focused ways:

1. add repeated trials per task
2. add stronger automated graders for structured tool outcomes

## Problem

The current harness runs each task once and mostly grades substrings, file contents, tool selection, and diff shape. That is enough for smoke validation, but not enough for stable regression tracking or reliable task-success measurement.

## Scope

This design covers only:

1. `trials_per_task`
2. stronger tool-outcome graders

## Non-Goals

This phase does not include task-pack expansion, LLM judges, human review workflows, CI gates, or a generic grader DSL.

## Proposed Design

### Task Spec Extension

Add `trials_per_task: int = 1` to `BenchmarkTaskSpec`.

Rules:

- minimum `1`
- maximum `20`
- `1` preserves current behavior
- each trial runs in fresh isolated conditions

### Trial Results

Keep per-run evidence. Add a new `BenchmarkTrialResult` for single-run outcomes.

`BenchmarkTaskResult` becomes an aggregate with:

- `trial_count`
- `pass_count`
- `partial_count`
- `fail_count`
- `invalid_run_count`
- `first_pass_label`
- `trials`

Aggregate label rules:

- `pass` if all trials pass
- `partial` if results are mixed or any trial is partial
- `fail` if all trials fail
- `invalid_run` if all trials are invalid

### Suite Metrics

Add:

- `total_trials`
- `first_pass_success_count`
- `first_pass_success_rate`

### New Grader Kinds

Add:

- `tool_status_is`
- `tool_output_contains_all`
- `tool_output_not_contains_any`

These use `tool_name` and inspect the latest matching tool execution by default.

Primary use case:

- prove that `run_test` actually succeeded and emitted `exit_code=0`

## Runner Behavior

`EvalRunner.run_task()` should:

1. execute `trials_per_task` independent runs
2. keep existing workspace preparation and evidence capture per trial
3. aggregate trial labels and counts into one task result

Existing tasks remain valid and opt into the new behavior only when they set new fields or new check kinds.

## Initial Task Migration

Do not migrate the whole task pack in this phase. Update only coding tasks that currently rely on `tool_used=run_test` so they also assert:

- `tool_status_is` for `run_test`
- `tool_output_contains_all` with `exit_code=0`

## Testing

Add coverage for:

- `trials_per_task` validation
- aggregate label computation
- first-pass metrics
- new grader validation rules
- `run_test` success and failure grading
- backward compatibility for existing tasks

## Acceptance Criteria

- existing tasks still run unchanged
- a task can run multiple trials and emit one aggregated result
- reports expose total trials and first-pass success rate
- coding tasks can assert that `run_test` actually passed

## Risks

- repeated trials increase runtime and report size
- aggregate labels can hide useful detail if trial evidence is not preserved

Control:

- keep per-trial evidence
- default `trials_per_task` to `1`
- migrate only a small number of tasks first
