from __future__ import annotations

from datetime import datetime, timezone
from typing import Literal

from pydantic import BaseModel, Field, model_validator

from app.schemas.approvals import ApprovalSummary
from app.schemas.events import TimelineEvent
from app.schemas.tools import ToolExecutionSummary
from app.schemas.turns import TurnSummary

ApprovalPolicyMode = Literal[
    "deny_all_shell",
    "allow_shell",
    "allow_shell_if_pattern_matches",
    "manual_required",
]

BenchmarkCheckKind = Literal[
    "final_text_contains_all",
    "final_text_not_contains_any",
    "file_contains_all",
    "file_not_contains_any",
    "latest_reflection_verdict_in",
    "latest_reflection_reason_codes_include_any",
    "tool_status_is",
    "tool_output_contains_all",
    "tool_output_not_contains_any",
    "max_total_approval_count",
    "max_pending_approval_count",
    "tool_used",
    "tool_used_any_of",
    "tool_not_used",
    "terminal_turn_status_in",
    "session_status_in",
    "workspace_diff_is_empty",
    "workspace_diff_not_empty",
]

BenchmarkLabel = Literal["pass", "partial", "fail", "invalid_run"]

BenchmarkFailureTag = Literal[
    "task_understanding",
    "context_miss",
    "wrong_tool_choice",
    "edit_failure",
    "verification_missing",
    "approval_stall",
    "workspace_violation",
    "subagent_coordination",
    "runtime_recovery",
    "iteration_exhausted",
]


class ApprovalPolicySpec(BaseModel):
    mode: ApprovalPolicyMode = "manual_required"
    allow_patterns: list[str] = Field(default_factory=list)
    feedback_message: str = ""

    @model_validator(mode="after")
    def validate_policy(self) -> "ApprovalPolicySpec":
        self.allow_patterns = [item.strip() for item in self.allow_patterns if item and item.strip()]
        self.feedback_message = self.feedback_message.strip()
        if self.mode == "allow_shell_if_pattern_matches" and not self.allow_patterns:
            raise ValueError("allow_shell_if_pattern_matches requires at least one allow pattern.")
        return self


class BenchmarkCheckSpec(BaseModel):
    kind: BenchmarkCheckKind
    path: str | None = None
    contains: list[str] = Field(default_factory=list)
    excludes: list[str] = Field(default_factory=list)
    tool_name: str | None = None
    tool_names: list[str] = Field(default_factory=list)
    statuses: list[str] = Field(default_factory=list)
    max_count: int | None = None
    occurrence: int | None = None

    @model_validator(mode="after")
    def validate_check(self) -> "BenchmarkCheckSpec":
        self.contains = [item.strip() for item in self.contains if item and item.strip()]
        self.excludes = [item.strip() for item in self.excludes if item and item.strip()]
        self.path = self.path.strip() if self.path else None
        self.tool_name = self.tool_name.strip() if self.tool_name else None
        self.tool_names = [item.strip() for item in self.tool_names if item and item.strip()]
        self.statuses = [item.strip() for item in self.statuses if item and item.strip()]
        if self.occurrence is not None and self.occurrence < 1:
            raise ValueError("occurrence must be a positive integer when provided.")
        if self.kind == "final_text_contains_all" and not self.contains:
            raise ValueError("final_text_contains_all requires at least one expected substring.")
        if self.kind == "final_text_not_contains_any" and not self.excludes:
            raise ValueError("final_text_not_contains_any requires at least one forbidden substring.")
        if self.kind == "file_contains_all":
            if not self.path or not self.contains:
                raise ValueError("file_contains_all requires path and at least one expected substring.")
        if self.kind == "file_not_contains_any":
            if not self.path or not self.excludes:
                raise ValueError("file_not_contains_any requires path and at least one forbidden substring.")
        if self.kind == "latest_reflection_verdict_in" and not self.statuses:
            raise ValueError("latest_reflection_verdict_in requires at least one status.")
        if self.kind == "latest_reflection_reason_codes_include_any" and not self.contains:
            raise ValueError("latest_reflection_reason_codes_include_any requires at least one reason code.")
        if self.kind == "tool_status_is":
            if not self.tool_name or not self.statuses:
                raise ValueError("tool_status_is requires tool_name and at least one status.")
        if self.kind == "tool_output_contains_all":
            if not self.tool_name or not self.contains:
                raise ValueError("tool_output_contains_all requires tool_name and at least one expected substring.")
        if self.kind == "tool_output_not_contains_any":
            if not self.tool_name or not self.excludes:
                raise ValueError("tool_output_not_contains_any requires tool_name and at least one forbidden substring.")
        if self.kind in {"tool_used", "tool_not_used"} and not self.tool_name:
            raise ValueError(f"{self.kind} requires tool_name.")
        if self.kind == "tool_used_any_of" and not self.tool_names:
            raise ValueError("tool_used_any_of requires at least one tool name.")
        if self.kind in {"terminal_turn_status_in", "session_status_in"} and not self.statuses:
            raise ValueError(f"{self.kind} requires at least one status.")
        if self.kind in {"max_total_approval_count", "max_pending_approval_count"}:
            if self.max_count is None or self.max_count < 0:
                raise ValueError(f"{self.kind} requires a non-negative max_count.")
        return self


class BenchmarkTaskSpec(BaseModel):
    id: str = Field(min_length=1, max_length=120)
    name: str = Field(min_length=1, max_length=200)
    user_prompt: str = Field(min_length=1)
    workspace_path: str | None = None
    workspace_fixture: str | None = None
    workspace_mode: Literal["bound", "default"] = "bound"
    execution_mode: Literal["normal", "plan"] = "normal"
    approval_policy: ApprovalPolicySpec = Field(default_factory=ApprovalPolicySpec)
    time_budget_seconds: int = Field(default=180, ge=1, le=3600)
    initialize_git: bool = False
    trials_per_task: int = Field(default=1, ge=1, le=20)
    tags: list[str] = Field(default_factory=list)
    expected_checks: list[BenchmarkCheckSpec] = Field(default_factory=list)
    notes: str = ""

    @model_validator(mode="after")
    def validate_task(self) -> "BenchmarkTaskSpec":
        self.user_prompt = self.user_prompt.strip()
        self.workspace_path = self.workspace_path.strip() if self.workspace_path else None
        self.workspace_fixture = self.workspace_fixture.strip() if self.workspace_fixture else None
        self.tags = [item.strip() for item in self.tags if item and item.strip()]
        self.notes = self.notes.strip()
        if not self.expected_checks:
            raise ValueError("Benchmark tasks require at least one expected check.")
        if self.workspace_path and self.workspace_fixture:
            raise ValueError("Benchmark tasks must specify either workspace_path or workspace_fixture, not both.")
        return self


class BenchmarkCheckResult(BaseModel):
    kind: BenchmarkCheckKind
    passed: bool
    message: str


class BenchmarkRunEvidence(BaseModel):
    task_id: str
    session_id: str | None = None
    effective_workspace_path: str | None = None
    started_at: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    finished_at: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    duration_seconds: float = 0.0
    timed_out: bool = False
    run_error: str | None = None
    session_status: str | None = None
    terminal_turn_status: str | None = None
    latest_turn_error_summary: str | None = None
    latest_turn_resume_hint: str | None = None
    latest_reflection_verdict: str | None = None
    latest_reflection_reason_codes: list[str] = Field(default_factory=list)
    latest_reflection_summary: str | None = None
    latest_reflection_next_action_prompt: str | None = None
    reviewer_retry_count: int = 0
    reviewer_stalled: bool = False
    reviewer_uncertainty_required: bool = False
    final_assistant_message: str = ""
    pending_approval_count: int = 0
    initial_workspace_status_text: str | None = None
    initial_workspace_diff_text: str | None = None
    workspace_status_text: str | None = None
    workspace_diff_text: str | None = None
    workspace_diff_is_empty: bool | None = None
    approvals: list[ApprovalSummary] = Field(default_factory=list)
    timeline_events: list[TimelineEvent] = Field(default_factory=list)
    tool_executions: list[ToolExecutionSummary] = Field(default_factory=list)
    turns: list[TurnSummary] = Field(default_factory=list)


class BenchmarkTrialResult(BaseModel):
    trial_index: int = Field(ge=1)
    task_id: str
    task_name: str
    label: BenchmarkLabel
    primary_failure_tag: BenchmarkFailureTag | None = None
    timed_out: bool = False
    duration_seconds: float = 0.0
    session_id: str | None = None
    checks: list[BenchmarkCheckResult] = Field(default_factory=list)
    evidence: BenchmarkRunEvidence


class BenchmarkTaskResult(BaseModel):
    task_id: str
    task_name: str
    label: BenchmarkLabel
    primary_failure_tag: BenchmarkFailureTag | None = None
    timed_out: bool = False
    duration_seconds: float = 0.0
    trial_count: int = 0
    pass_count: int = 0
    partial_count: int = 0
    fail_count: int = 0
    invalid_run_count: int = 0
    first_pass_label: BenchmarkLabel | None = None
    trials: list[BenchmarkTrialResult] = Field(default_factory=list)


class BenchmarkSuiteReport(BaseModel):
    started_at: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    finished_at: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    duration_seconds: float = 0.0
    total_tasks: int = 0
    total_trials: int = 0
    first_pass_success_count: int = 0
    first_pass_success_rate: float = 0.0
    counts_by_label: dict[BenchmarkLabel, int] = Field(default_factory=dict)
    counts_by_failure_tag: dict[str, int] = Field(default_factory=dict)
    counts_by_reflection_verdict: dict[str, int] = Field(default_factory=dict)
    counts_by_tag_and_label: dict[str, dict[str, int]] = Field(default_factory=dict)
    results: list[BenchmarkTaskResult] = Field(default_factory=list)
