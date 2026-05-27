from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class TaskObligation:
    id: str
    kind: str
    description: str
    critical: bool = True


@dataclass(frozen=True)
class TaskProfile:
    task_kinds: list[str]
    verify_level: str
    completion_mode: str
    risk_level: str
    obligations: list[TaskObligation]


def build_task_profile(
    *,
    latest_request: str,
    requires_code_change: bool,
    requires_dependency_install: bool,
    requires_external_fact_lookup: bool,
    requires_read_only_analysis: bool = False,
) -> TaskProfile:
    task_kinds: list[str] = []
    obligations: list[TaskObligation] = []
    verify_level = "none"
    completion_mode = "direct"
    risk_level = "low"

    if requires_dependency_install:
        task_kinds.append("dependency_install")
        verify_level = "hard"
        completion_mode = "goal_driven"
        risk_level = "high"
        obligations.extend(
            [
                TaskObligation(
                    id="dependency_installed_in_target_environment",
                    kind="environment_state",
                    description="The requested dependency is installed in the target project environment.",
                ),
                TaskObligation(
                    id="dependency_verified_in_target_environment",
                    kind="behavior_runtime",
                    description="The requested dependency is verified from inside the target project environment.",
                ),
            ]
        )

    if requires_code_change:
        task_kinds.append("code_change")
        verify_level = "hard"
        completion_mode = "goal_driven"
        risk_level = "high" if risk_level == "high" else "medium"
        obligations.extend(
            [
                TaskObligation(
                    id="requested_code_change_applied",
                    kind="artifact_state",
                    description="The requested code change has been applied to the workspace.",
                ),
                TaskObligation(
                    id="requested_code_change_verified",
                    kind="behavior_runtime",
                    description="The requested code change has been verified strongly enough for completion.",
                ),
            ]
        )

    if requires_external_fact_lookup:
        task_kinds.append("external_fact_lookup")
        verify_level = "hard"
        completion_mode = "goal_driven"
        if risk_level == "low":
            risk_level = "medium"
        obligations.append(
            TaskObligation(
                id="external_fact_verified",
                kind="external_fact",
                description="The requested external fact is supported by sufficiently fresh evidence.",
            )
        )

    if not task_kinds and requires_read_only_analysis:
        task_kinds.append("read_only_analysis")
        verify_level = "soft"
        completion_mode = "evidence_check"
        risk_level = "low"

    if not task_kinds and latest_request.strip():
        verify_level = "none"
        completion_mode = "direct"
        risk_level = "low"

    return TaskProfile(
        task_kinds=task_kinds,
        verify_level=verify_level,
        completion_mode=completion_mode,
        risk_level=risk_level,
        obligations=obligations,
    )
