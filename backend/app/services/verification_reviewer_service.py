from __future__ import annotations

import json
import re
import shlex
from dataclasses import dataclass
from pathlib import Path

from app.core.config import settings
from app.providers import ProviderConfigError, ProviderRequestError, TextBlock, create_client
from app.services.verification_packet_service import VerificationPacket


@dataclass(frozen=True)
class ReviewResult:
    verdict: str
    goal_assessment: str
    supported_claims: list[str]
    unsupported_claims: list[str]
    next_verification_action: str | None
    user_visible_uncertainty: str | None
    reason_codes: list[str]
    next_phase: str = "finalize"


ALLOWED_VERDICTS = {
    "done",
    "done_with_uncertainty",
    "continue_with_read_only_evidence",
    "continue_with_repair",
    "continue_with_verification",
    "blocked",
    "blocked_uncertain",
}


def review_packet(
    packet: VerificationPacket,
    *,
    response_has_blocker: bool,
) -> ReviewResult:
    llm_result = _review_with_llm(packet, response_has_blocker=response_has_blocker)
    if llm_result is not None:
        return llm_result
    return _fallback_review(packet, response_has_blocker=response_has_blocker)


def _review_with_llm(
    packet: VerificationPacket,
    *,
    response_has_blocker: bool,
) -> ReviewResult | None:
    if not settings.model_id:
        return None
    prompt = _review_prompt()
    payload = {
        "original_goal": packet.original_goal,
        "completion_mode": packet.task_profile.completion_mode,
        "candidate_result_summary": packet.candidate_result_summary,
        "current_result_summary": packet.current_result_summary,
        "artifact_summary": packet.artifact_summary,
        "evidence_summary": packet.evidence_summary,
        "open_verification_gaps": packet.open_verification_gaps,
        "blockers": packet.blockers,
        "repairable_blockers": [
            {
                "kind": blocker.kind,
                "subject": blocker.subject,
                "detail": blocker.detail,
            }
            for blocker in packet.repairable_blockers
        ],
        "uncertainty_already_stated": packet.uncertainty_already_stated,
        "remaining_repair_attempts": packet.remaining_repair_attempts,
        "remaining_verify_attempts": packet.remaining_verify_attempts,
        "remaining_auto_verify_attempts": packet.remaining_auto_verify_attempts,
        "last_failed_action": packet.last_failed_action,
        "last_failed_verification_command": packet.last_failed_verification_command,
        "response_has_blocker": response_has_blocker,
    }
    try:
        response = create_client().messages.create(
            model=settings.model_id,
            system=prompt,
            messages=[{"role": "user", "content": json.dumps(payload, ensure_ascii=True)}],
            max_tokens=500,
        )
    except (ProviderConfigError, ProviderRequestError, Exception):
        return None

    raw_text = " ".join(
        block.text.strip()
        for block in response.content
        if isinstance(block, TextBlock) and block.text.strip()
    ).strip()
    if not raw_text:
        return None
    return _parse_review_result(raw_text, packet, response_has_blocker=response_has_blocker)


def _parse_review_result(
    raw_text: str,
    packet: VerificationPacket,
    *,
    response_has_blocker: bool,
) -> ReviewResult | None:
    try:
        start = raw_text.index("{")
        end = raw_text.rindex("}") + 1
    except ValueError:
        return None
    try:
        payload = json.loads(raw_text[start:end])
    except Exception:
        return None
    if not isinstance(payload, dict):
        return None

    verdict = str(payload.get("verdict") or "").strip().lower()
    if verdict not in ALLOWED_VERDICTS:
        return None
    if verdict == "blocked_uncertain":
        verdict = "blocked"

    supported_claims = _string_list(payload.get("supported_claims"))
    unsupported_claims = _string_list(payload.get("unsupported_claims"))
    next_action = _optional_text(payload.get("next_verification_action"))
    uncertainty = _optional_text(payload.get("user_visible_uncertainty"))
    goal_assessment = _optional_text(payload.get("goal_assessment")) or "Reviewer completed."

    if verdict == "continue_with_read_only_evidence":
        if packet.task_profile.completion_mode != "evidence_check":
            return None
        if not next_action:
            return None
    if verdict == "continue_with_repair":
        if packet.task_profile.completion_mode != "goal_driven":
            return None
        if packet.remaining_repair_attempts <= 0:
            return None
        if not next_action:
            return None
    if verdict == "continue_with_verification":
        if packet.remaining_verify_attempts <= 0 or packet.weak_verification_stalled:
            return None
        if not next_action:
            return None
    if verdict in {"blocked", "done_with_uncertainty"} and not uncertainty:
        return None
    if verdict == "done":
        if response_has_blocker:
            return None
        if packet.open_verification_gaps:
            return None

    return ReviewResult(
        verdict=verdict,
        goal_assessment=goal_assessment,
        supported_claims=supported_claims,
        unsupported_claims=unsupported_claims,
        next_verification_action=next_action,
        user_visible_uncertainty=uncertainty,
        reason_codes=_reason_codes_for(
            packet,
            response_has_blocker=response_has_blocker,
            verdict=verdict,
        ),
        next_phase=_next_phase_for_verdict(verdict),
    )


def _fallback_review(
    packet: VerificationPacket,
    *,
    response_has_blocker: bool,
) -> ReviewResult:
    if packet.task_profile.completion_mode == "direct":
        return ReviewResult(
            verdict="done",
            goal_assessment="Direct completion does not require structured review.",
            supported_claims=[],
            unsupported_claims=[],
            next_verification_action=None,
            user_visible_uncertainty=None,
            reason_codes=[],
            next_phase="finalize",
        )
    if packet.task_profile.completion_mode == "evidence_check":
        return _fallback_soft_review(packet, response_has_blocker=response_has_blocker)
    return _fallback_hard_review(packet, response_has_blocker=response_has_blocker)


def _fallback_soft_review(
    packet: VerificationPacket,
    *,
    response_has_blocker: bool,
) -> ReviewResult:
    supported_claims = _supported_claims(packet)
    unsupported_claims: list[str] = []
    if not packet.current_result_summary.strip():
        unsupported_claims.append("the current result does not yet answer the original read-only request")
    goal_assessment = _goal_assessment(packet, unsupported_claims, response_has_blocker=response_has_blocker)

    if response_has_blocker:
        return ReviewResult(
            verdict="blocked",
            goal_assessment=goal_assessment,
            supported_claims=supported_claims,
            unsupported_claims=unsupported_claims or ["the task is blocked before the read-only answer was completed"],
            next_verification_action=None,
            user_visible_uncertainty=_blocked_uncertainty(packet, response_has_blocker=True),
            reason_codes=_reason_codes_for(
                packet,
                response_has_blocker=response_has_blocker,
                verdict="blocked",
            ),
            next_phase="blocked",
        )

    if unsupported_claims:
        return ReviewResult(
            verdict="continue_with_read_only_evidence",
            goal_assessment=goal_assessment,
            supported_claims=supported_claims,
            unsupported_claims=unsupported_claims,
            next_verification_action=(
                "Use one additional read-only step to gather the missing evidence for the user's question, "
                "then answer directly without mutating files or the environment."
            ),
            user_visible_uncertainty=None,
            reason_codes=_reason_codes_for(
                packet,
                response_has_blocker=response_has_blocker,
                verdict="continue_with_read_only_evidence",
            ),
            next_phase="gather_evidence",
        )

    if packet.uncertainty_already_stated:
        return ReviewResult(
            verdict="done_with_uncertainty",
            goal_assessment="The read-only answer is ready and already communicates uncertainty.",
            supported_claims=supported_claims,
            unsupported_claims=[],
            next_verification_action=None,
            user_visible_uncertainty=packet.current_result_summary.strip(),
            reason_codes=[],
            next_phase="finalize",
        )

    return ReviewResult(
        verdict="done",
        goal_assessment="The read-only answer is ready to return.",
        supported_claims=supported_claims,
        unsupported_claims=[],
        next_verification_action=None,
        user_visible_uncertainty=None,
        reason_codes=[],
        next_phase="finalize",
    )


def _fallback_hard_review(
    packet: VerificationPacket,
    *,
    response_has_blocker: bool,
) -> ReviewResult:
    supported_claims = _supported_claims(packet)
    unsupported_claims = list(packet.open_verification_gaps)
    goal_assessment = _goal_assessment(packet, unsupported_claims, response_has_blocker=response_has_blocker)

    if response_has_blocker:
        return ReviewResult(
            verdict="blocked_uncertain",
            goal_assessment=goal_assessment,
            supported_claims=supported_claims,
            unsupported_claims=unsupported_claims or ["the task is blocked before proof was completed"],
            next_verification_action=None,
            user_visible_uncertainty=_blocked_uncertainty(packet, response_has_blocker=True),
            reason_codes=_reason_codes_for(
                packet,
                response_has_blocker=response_has_blocker,
                verdict="blocked_uncertain",
            ),
            next_phase="blocked",
        )

    if not unsupported_claims:
        return ReviewResult(
            verdict="done_with_uncertainty" if packet.uncertainty_already_stated else "done",
            goal_assessment=goal_assessment,
            supported_claims=supported_claims,
            unsupported_claims=[],
            next_verification_action=None,
            user_visible_uncertainty=(packet.current_result_summary.strip() if packet.uncertainty_already_stated else None),
            reason_codes=[],
            next_phase="finalize",
        )

    action = _repair_action(packet)
    if action:
        return ReviewResult(
            verdict="continue_with_repair",
            goal_assessment=goal_assessment,
            supported_claims=supported_claims,
            unsupported_claims=unsupported_claims,
            next_verification_action=action,
            user_visible_uncertainty=None,
            reason_codes=_reason_codes_for(
                packet,
                response_has_blocker=response_has_blocker,
                verdict="continue_with_repair",
            ),
            next_phase="repair",
        )

    action = _next_verification_action(packet)
    if action and packet.remaining_verify_attempts > 0 and not packet.weak_verification_stalled:
        return ReviewResult(
            verdict="continue_with_verification",
            goal_assessment=goal_assessment,
            supported_claims=supported_claims,
            unsupported_claims=unsupported_claims,
            next_verification_action=action,
            user_visible_uncertainty=None,
            reason_codes=_reason_codes_for(
                packet,
                response_has_blocker=response_has_blocker,
                verdict="continue_with_verification",
            ),
            next_phase="verify",
        )

    return ReviewResult(
        verdict="blocked_uncertain",
        goal_assessment=goal_assessment,
        supported_claims=supported_claims,
        unsupported_claims=unsupported_claims,
        next_verification_action=None,
        user_visible_uncertainty=_blocked_uncertainty(packet, response_has_blocker=False),
        reason_codes=_reason_codes_for(
            packet,
            response_has_blocker=response_has_blocker,
            verdict="blocked_uncertain",
        ),
        next_phase="blocked",
    )


def _supported_claims(packet: VerificationPacket) -> list[str]:
    claims: list[str] = []
    if packet.has_successful_write_tool:
        claims.append("the requested workspace artifacts were changed")
    if packet.verification_state == "sufficient":
        claims.append("strong verification evidence is available")
    elif packet.verification_state == "weak":
        claims.append("some verification evidence is available, but it is weak")
    if packet.web_search_succeeded:
        quality = packet.web_search_evidence_quality or "unknown"
        claims.append(f"web_search returned evidence with quality={quality}")
    return claims


def _goal_assessment(
    packet: VerificationPacket,
    unsupported_claims: list[str],
    *,
    response_has_blocker: bool,
) -> str:
    if response_has_blocker:
        return "The current result is blocked before the original goal is fully supported."
    if unsupported_claims:
        return "The current result is not yet fully supported against the original goal."
    return "The current result is sufficiently supported for the original goal."


def _next_verification_action(packet: VerificationPacket) -> str | None:
    if "external_fact_lookup" in packet.task_profile.task_kinds:
        if not packet.web_search_succeeded:
            return (
                "Run web_search for the specific external fact now, using a fresh authoritative source when possible, "
                "then confirm the result or state explicit uncertainty."
            )
        if packet.web_search_evidence_quality == "weak":
            return (
                "Run one stronger web_search query targeting an authoritative fresh source, "
                "then either confirm the fact or explicitly state uncertainty."
            )
    if "dependency_install" in packet.task_profile.task_kinds:
        return (
            "Run one concrete target-environment verification with run_test to prove the dependency is available "
            "from the project interpreter instead of relying on file edits alone."
        )
    if "code_change" in packet.task_profile.task_kinds:
        if not packet.has_successful_write_tool:
            return (
                "Apply the requested code change now, then use run_test for one concrete verification step that exercises the changed path."
            )
        if packet.verification_state in {"none", "weak", "conflicting"}:
            script_target = _script_target_path(packet)
            if script_target and _needs_python_smoke_check(packet):
                return (
                    f"Use run_test for a concrete smoke check of `{script_target}` now. "
                    f"Prefer argv such as ['python3', '{script_target}', '--help'] if it exposes a CLI; "
                    "otherwise run a tiny synthetic input path that exercises the main code path. "
                    "Do not stop at py_compile alone."
                )
            if _needs_python_smoke_check(packet):
                return (
                    "Use run_test for a concrete smoke check of the changed Python entry point now. "
                    "Prefer running it with --help if it exposes a CLI; otherwise run a tiny synthetic input path "
                    "or import path that exercises the main code path. Do not stop at py_compile alone."
                )
            return (
                "Use run_test for one stronger verification step that exercises the changed code path, "
                "not just syntax validation, and report the concrete outcome."
            )
    return None


def _repair_action(packet: VerificationPacket) -> str | None:
    if packet.remaining_repair_attempts <= 0:
        return None
    missing_module = next((blocker.subject for blocker in packet.repairable_blockers if blocker.kind == "missing_python_module"), None)
    if missing_module:
        install_command = _missing_module_install_command(packet, missing_module)
        rerun = (
            f" After the install step, rerun the same verification command: {packet.last_failed_verification_command}."
            if packet.last_failed_verification_command
            else " After the install step, rerun the failed verification with run_test."
        )
        return (
            f"Use bash to install `{missing_module}` into the target project environment now with this exact command: `{install_command}`."
            f"{rerun} Do not stop at reporting the missing module."
        )
    wrong_environment = any(blocker.kind == "wrong_python_environment" for blocker in packet.repairable_blockers)
    if wrong_environment and packet.last_failed_verification_command:
        return (
            "Your last verification ran in the wrong Python environment. "
            f"Switch to the target project interpreter or environment, then rerun the same verification command: {packet.last_failed_verification_command}."
        )
    if wrong_environment:
        return (
            "Your last verification ran in the wrong Python environment. "
            "Switch to the target project interpreter or environment, then rerun the failed verification with run_test."
        )
    return None


def _blocked_uncertainty(packet: VerificationPacket, *, response_has_blocker: bool) -> str:
    if response_has_blocker and packet.current_result_summary.strip():
        return packet.current_result_summary.strip()
    if packet.open_verification_gaps:
        joined = "; ".join(packet.open_verification_gaps)
        return (
            "I cannot confidently claim the task is complete. "
            f"Remaining proof gap: {joined}."
        )
    return "I cannot confidently claim the task is complete with the evidence gathered so far."


def _missing_module_install_command(packet: VerificationPacket, missing_module: str) -> str:
    verification_command = list(packet.last_failed_verification_command)
    if verification_command:
        python_candidate = verification_command[0].strip()
        if python_candidate:
            python_name = Path(python_candidate).name.lower()
            if python_name.startswith("python"):
                return shlex.join([python_candidate, "-m", "pip", "install", missing_module])
    return shlex.join(["python3", "-m", "pip", "install", missing_module])


def _script_target_path(packet: VerificationPacket) -> str | None:
    candidates = packet.artifact_summary + [packet.current_result_summary]
    for item in candidates:
        match = re.search(r"([A-Za-z0-9_./-]+\.py)\b", item)
        if match:
            return match.group(1)
    return None


def _needs_python_smoke_check(packet: VerificationPacket) -> bool:
    if packet.verification_state not in {"none", "weak", "conflicting"}:
        return False
    if not any(".py" in item for item in packet.artifact_summary + [packet.current_result_summary, packet.latest_request, packet.original_goal]):
        return False
    verification_payloads = [
        item.payload
        for item in packet.tool_results
        if item.tool_name == "run_test" and isinstance(item.payload, dict)
    ]
    if not verification_payloads:
        return True
    return all(
        payload.get("classification") != "verification"
        or payload.get("verification_kind") in {"syntax_check", "package_probe"}
        for payload in verification_payloads
    )


def _reason_codes_for(
    packet: VerificationPacket,
    *,
    response_has_blocker: bool,
    verdict: str,
) -> list[str]:
    codes: list[str] = []
    if response_has_blocker:
        codes.append("blocked_by_reported_issue")
    if packet.weak_verification_stalled:
        codes.append("verification_stalled")
    if packet.repairable_blockers and verdict == "continue_with_repair":
        codes.append("repairable_blocker")
    if "external_fact_lookup" in packet.task_profile.task_kinds and packet.open_verification_gaps:
        codes.append("missing_fresh_evidence")
    if any(kind in packet.task_profile.task_kinds for kind in {"code_change", "dependency_install"}) and packet.open_verification_gaps:
        codes.append("verification_gap")
    if verdict in {"blocked", "blocked_uncertain"}:
        codes.append("blocked_uncertain")
    return codes


def _review_prompt() -> str:
    return (
        "You are Jarvis's lightweight verification reviewer.\n"
        "Review whether the candidate final result satisfies the user's original goal.\n"
        "Prefer caution over confident completion.\n"
        "Return JSON only with keys: verdict, goal_assessment, supported_claims, unsupported_claims, "
        "next_verification_action, user_visible_uncertainty.\n"
        "Allowed verdict values: done, done_with_uncertainty, continue_with_read_only_evidence, "
        "continue_with_repair, continue_with_verification, blocked.\n"
        "Use continue_with_read_only_evidence only for read-only follow-up evidence gathering.\n"
        "Use continue_with_repair only when exactly one bounded repair step is likely to unblock the goal.\n"
        "Use continue_with_verification only when one additional targeted verification step is likely to close the key gap.\n"
        "Use blocked when proof remains insufficient, repetitive, or blocked, and provide user_visible_uncertainty.\n"
        "Do not output markdown."
    )


def _next_phase_for_verdict(verdict: str) -> str:
    normalized = (verdict or "").strip().lower()
    if normalized == "continue_with_read_only_evidence":
        return "gather_evidence"
    if normalized == "continue_with_repair":
        return "repair"
    if normalized == "continue_with_verification":
        return "verify"
    if normalized in {"blocked", "blocked_uncertain"}:
        return "blocked"
    return "finalize"


def _string_list(value: object) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(item).strip() for item in value if str(item).strip()]


def _optional_text(value: object) -> str | None:
    text = str(value or "").strip()
    return text or None
