from __future__ import annotations

from dataclasses import dataclass

from app.services.task_profile_service import TaskProfile


@dataclass(frozen=True)
class ToolResultEvidence:
    tool_name: str
    status: str
    content: str
    payload: dict[str, object] | None = None


@dataclass(frozen=True)
class RepairableBlocker:
    kind: str
    subject: str | None = None
    detail: str | None = None


@dataclass(frozen=True)
class VerificationPacket:
    task_profile: TaskProfile
    original_goal: str
    candidate_result_summary: str
    current_result_summary: str
    artifact_summary: list[str]
    evidence_summary: list[str]
    open_verification_gaps: list[str]
    blockers: list[str]
    repairable_blockers: list[RepairableBlocker]
    uncertainty_already_stated: bool
    remaining_repair_attempts: int
    remaining_verify_attempts: int
    remaining_auto_verify_attempts: int
    latest_request: str
    tool_results: list[ToolResultEvidence]
    inspected_tool_names: set[str]
    has_successful_write_tool: bool
    verification_state: str
    last_failed_action: str | None
    last_failed_verification_command: list[str]
    run_test_attempt_count: int
    verification_attempt_count: int
    weak_verification_stalled: bool
    web_search_attempted: bool
    web_search_succeeded: bool
    web_search_evidence_quality: str | None


WRITE_TOOL_NAMES = {"write_file", "edit_file", "apply_patch"}
READ_ONLY_TOOL_NAMES = {
    "conversation_search",
    "get_session_git_state",
    "list_files",
    "list_session_assets",
    "list_skills",
    "load_skill",
    "memory_search",
    "read_asset_chunk",
    "read_asset_summary",
    "read_file",
    "read_file_range",
    "search_asset_chunks",
    "search_text",
    "show_diff",
    "show_status",
    "web_search",
}


def build_verification_packet(
    *,
    task_profile: TaskProfile,
    latest_request: str,
    tool_results: list[ToolResultEvidence],
    web_search_evidence_quality: str | None,
    original_goal: str | None = None,
    current_result_summary: str = "",
    uncertainty_already_stated: bool = False,
    remaining_repair_attempts: int = 1,
    remaining_auto_verify_attempts: int = 1,
) -> VerificationPacket:
    inspected_tool_names = {item.tool_name for item in tool_results}
    has_successful_write_tool = any(
        item.tool_name in WRITE_TOOL_NAMES and item.status == "completed" for item in tool_results
    )
    verification_state = classify_verification_state(tool_results)
    run_test_attempt_count = sum(1 for item in tool_results if item.tool_name == "run_test")
    verification_attempt_count = sum(
        1
        for item in tool_results
        if item.tool_name == "run_test"
        and isinstance(item.payload, dict)
        and item.payload.get("classification") == "verification"
    )
    web_search_attempted = any(item.tool_name == "web_search" for item in tool_results)
    web_search_succeeded = any(
        item.tool_name == "web_search" and item.status == "completed" for item in tool_results
    )
    weak_verification_stalled = _weak_verification_stalled(tool_results)
    blockers = _blockers(tool_results)
    repairable_blockers = _repairable_blockers(tool_results)
    last_failed_action = _last_failed_action(tool_results)
    last_failed_verification_command = _last_failed_verification_command(tool_results)
    return VerificationPacket(
        task_profile=task_profile,
        original_goal=(original_goal or latest_request).strip(),
        candidate_result_summary=current_result_summary.strip(),
        current_result_summary=current_result_summary.strip(),
        artifact_summary=_artifact_summary(tool_results),
        evidence_summary=_evidence_summary(
            tool_results,
            web_search_evidence_quality=web_search_evidence_quality,
        ),
        open_verification_gaps=_open_verification_gaps(
            task_profile=task_profile,
            has_successful_write_tool=has_successful_write_tool,
            verification_state=verification_state,
            weak_verification_stalled=weak_verification_stalled,
            web_search_attempted=web_search_attempted,
            web_search_succeeded=web_search_succeeded,
            web_search_evidence_quality=web_search_evidence_quality,
        ),
        blockers=blockers,
        repairable_blockers=repairable_blockers,
        uncertainty_already_stated=uncertainty_already_stated,
        remaining_repair_attempts=max(0, int(remaining_repair_attempts)),
        remaining_verify_attempts=max(0, int(remaining_auto_verify_attempts)),
        remaining_auto_verify_attempts=max(0, int(remaining_auto_verify_attempts)),
        latest_request=latest_request,
        tool_results=tool_results,
        inspected_tool_names=inspected_tool_names,
        has_successful_write_tool=has_successful_write_tool,
        verification_state=verification_state,
        last_failed_action=last_failed_action,
        last_failed_verification_command=last_failed_verification_command,
        run_test_attempt_count=run_test_attempt_count,
        verification_attempt_count=verification_attempt_count,
        weak_verification_stalled=weak_verification_stalled,
        web_search_attempted=web_search_attempted,
        web_search_succeeded=web_search_succeeded,
        web_search_evidence_quality=web_search_evidence_quality,
    )


def classify_verification_state(tool_results: list[ToolResultEvidence]) -> str:
    state = "none"
    for item in tool_results:
        if item.tool_name != "run_test":
            continue
        payload = item.payload
        if not isinstance(payload, dict):
            continue
        if payload.get("classification") != "verification":
            continue
        if payload.get("wrong_environment"):
            state = "conflicting"
            continue
        if item.status != "completed":
            state = "conflicting"
            continue
        if payload.get("evidence_strength") == "sufficient":
            state = "sufficient"
            continue
        if payload.get("evidence_strength") == "weak" and state == "none":
            state = "weak"
    return state


def _artifact_summary(tool_results: list[ToolResultEvidence]) -> list[str]:
    artifacts: list[str] = []
    for item in tool_results:
        if item.tool_name not in WRITE_TOOL_NAMES or item.status != "completed":
            continue
        summary = _compact_text(item.content)
        if summary:
            artifacts.append(summary)
    return artifacts


def _evidence_summary(
    tool_results: list[ToolResultEvidence],
    *,
    web_search_evidence_quality: str | None,
) -> list[str]:
    evidence: list[str] = []
    for item in tool_results:
        payload = item.payload if isinstance(item.payload, dict) else {}
        if item.tool_name == "run_test" and payload.get("classification") == "verification":
            verification_kind = str(payload.get("verification_kind") or "verification")
            evidence_strength = str(payload.get("evidence_strength") or "unknown")
            wrong_environment = bool(payload.get("wrong_environment"))
            evidence.append(
                f"run_test classified as {verification_kind}, evidence_strength={evidence_strength}, "
                f"status={item.status}, wrong_environment={str(wrong_environment).lower()}"
            )
            continue
        if item.tool_name == "web_search":
            quality = (web_search_evidence_quality or "unknown").strip() or "unknown"
            evidence.append(f"web_search status={item.status}, evidence_quality={quality}")
    return evidence


def _open_verification_gaps(
    *,
    task_profile: TaskProfile,
    has_successful_write_tool: bool,
    verification_state: str,
    weak_verification_stalled: bool,
    web_search_attempted: bool,
    web_search_succeeded: bool,
    web_search_evidence_quality: str | None,
) -> list[str]:
    gaps: list[str] = []

    if "code_change" in task_profile.task_kinds:
        if not has_successful_write_tool:
            gaps.append("the requested code change has not been applied yet")
        elif verification_state == "none":
            gaps.append("the main execution path has not been exercised")
        elif verification_state == "weak":
            gaps.append("only weak verification evidence is available")
        elif verification_state == "conflicting":
            gaps.append("the latest verification failed or ran in the wrong environment")

    if "dependency_install" in task_profile.task_kinds:
        if verification_state == "none":
            gaps.append("the dependency was not verified in the target environment")
        elif verification_state == "weak":
            gaps.append("only weak target-environment verification is available")
        elif verification_state == "conflicting":
            gaps.append("the dependency verification failed or ran in the wrong environment")

    if "external_fact_lookup" in task_profile.task_kinds:
        if not web_search_succeeded:
            if web_search_attempted:
                gaps.append("the external fact search did not produce usable fresh evidence")
            else:
                gaps.append("the external fact lacks fresh supporting evidence")
        elif web_search_evidence_quality == "weak":
            gaps.append("the external fact only has weak supporting evidence")

    if weak_verification_stalled:
        gaps.append("repeated weak verification indicates the process is stalled")

    return gaps


def _blockers(tool_results: list[ToolResultEvidence]) -> list[str]:
    blockers: list[str] = []
    for item in tool_results:
        payload = item.payload if isinstance(item.payload, dict) else {}
        if item.status not in {"error", "blocked"} and not bool(payload.get("wrong_environment")):
            continue
        summary = _compact_text(item.content)
        if summary:
            blockers.append(f"{item.tool_name}: {summary}")
    return blockers


def _repairable_blockers(tool_results: list[ToolResultEvidence]) -> list[RepairableBlocker]:
    blockers: list[RepairableBlocker] = []
    for item in tool_results:
        payload = item.payload if isinstance(item.payload, dict) else {}
        if item.tool_name == "run_test":
            if payload.get("wrong_environment"):
                blockers.append(
                    RepairableBlocker(
                        kind="wrong_python_environment",
                        detail="The verification ran outside the target project environment.",
                    )
                )
            missing_module = _missing_python_module(item.content)
            if missing_module:
                blockers.append(
                    RepairableBlocker(
                        kind="missing_python_module",
                        subject=missing_module,
                        detail=f"Verification failed because Python could not import {missing_module}.",
                    )
                )
            if item.status == "blocked" and "Use bash with approval" in item.content:
                blockers.append(
                    RepairableBlocker(
                        kind="approval_required_for_shell",
                        detail="Repair requires an approval-gated shell mutation.",
                    )
                )
    return _dedupe_repairable_blockers(blockers)


def _last_failed_action(tool_results: list[ToolResultEvidence]) -> str | None:
    for item in reversed(tool_results):
        payload = item.payload if isinstance(item.payload, dict) else {}
        if item.status in {"error", "blocked"} or bool(payload.get("wrong_environment")):
            return item.tool_name
    return None


def _last_failed_verification_command(tool_results: list[ToolResultEvidence]) -> list[str]:
    for item in reversed(tool_results):
        if item.tool_name != "run_test":
            continue
        payload = item.payload if isinstance(item.payload, dict) else {}
        if item.status not in {"error", "blocked"} and not bool(payload.get("wrong_environment")):
            continue
        if payload.get("classification") != "verification":
            continue
        command = payload.get("resolved_command") or payload.get("original_command") or []
        if isinstance(command, list):
            return [str(part).strip() for part in command if str(part).strip()]
    return []


def _weak_verification_stalled(tool_results: list[ToolResultEvidence]) -> bool:
    weak_attempts = 0
    for item in tool_results:
        if item.tool_name != "run_test":
            continue
        payload = item.payload
        if not isinstance(payload, dict):
            continue
        if payload.get("classification") != "verification":
            continue
        if payload.get("evidence_strength") == "weak":
            weak_attempts += 1
    return weak_attempts >= 3


def _compact_text(text: str, limit: int = 160) -> str:
    normalized = " ".join(text.strip().split())
    if len(normalized) <= limit:
        return normalized
    return f"{normalized[: limit - 1].rstrip()}…"


def _missing_python_module(text: str) -> str | None:
    import re

    patterns = [
        r"No module named ['\"]([^'\"]+)['\"]",
        r"ModuleNotFoundError:\s+No module named ['\"]([^'\"]+)['\"]",
    ]
    for pattern in patterns:
        match = re.search(pattern, text)
        if match:
            return match.group(1).strip() or None
    return None


def _dedupe_repairable_blockers(blockers: list[RepairableBlocker]) -> list[RepairableBlocker]:
    deduped: list[RepairableBlocker] = []
    seen: set[tuple[str, str | None, str | None]] = set()
    for blocker in blockers:
        key = (blocker.kind, blocker.subject, blocker.detail)
        if key in seen:
            continue
        seen.add(key)
        deduped.append(blocker)
    return deduped
