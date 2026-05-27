from __future__ import annotations

from dataclasses import dataclass

from app.services.verification_packet_service import VerificationPacket


@dataclass(frozen=True)
class ObligationResult:
    obligation_id: str
    status: str
    why: str


@dataclass(frozen=True)
class VerificationResult:
    turn_verdict: str
    reason_codes: list[str]
    next_action_prompt: str
    summary: str
    obligation_results: list[ObligationResult]


def evaluate_packet(
    packet: VerificationPacket,
    *,
    final_text: str,
    response_has_blocker: bool,
    response_has_uncertainty: bool,
) -> VerificationResult:
    if "external_fact_lookup" in packet.task_profile.task_kinds:
        result = _evaluate_external_fact(
            packet,
            response_has_blocker=response_has_blocker,
            response_has_uncertainty=response_has_uncertainty,
        )
        if result is not None:
            return result

    if "dependency_install" in packet.task_profile.task_kinds:
        result = _evaluate_dependency_install(packet, response_has_blocker=response_has_blocker)
        if result is not None:
            return result

    if "code_change" in packet.task_profile.task_kinds:
        result = _evaluate_code_change(packet, response_has_blocker=response_has_blocker)
        if result is not None:
            return result

    if response_has_blocker:
        return VerificationResult(
            turn_verdict="blocked",
            reason_codes=[],
            next_action_prompt="",
            summary="The task is blocked by the issue described in the final answer.",
            obligation_results=[],
        )
    return VerificationResult(
        turn_verdict="done",
        reason_codes=[],
        next_action_prompt="",
        summary="The final answer is ready to return to the user.",
        obligation_results=[],
    )


def _evaluate_external_fact(
    packet: VerificationPacket,
    *,
    response_has_blocker: bool,
    response_has_uncertainty: bool,
) -> VerificationResult | None:
    if not packet.web_search_succeeded:
        if packet.web_search_attempted:
            if response_has_blocker:
                return VerificationResult(
                    turn_verdict="blocked",
                    reason_codes=[],
                    next_action_prompt="",
                    summary="The requested external fact could not be confirmed because web_search did not produce usable evidence.",
                    obligation_results=[
                        ObligationResult(
                            obligation_id="external_fact_verified",
                            status="blocked",
                            why="web_search was attempted but did not produce usable evidence",
                        )
                    ],
                )
            if response_has_uncertainty:
                return VerificationResult(
                    turn_verdict="done",
                    reason_codes=[],
                    next_action_prompt="",
                    summary="The final answer already communicates uncertainty after a failed web search attempt.",
                    obligation_results=[
                        ObligationResult(
                            obligation_id="external_fact_verified",
                            status="missing_evidence",
                            why="web_search evidence is incomplete but uncertainty was communicated",
                        )
                    ],
                )
        return VerificationResult(
            turn_verdict="continue",
            reason_codes=["wrong_tool_choice"],
            next_action_prompt=(
                "The user asked for a time-sensitive external fact. "
                "Call web_search before finalizing your answer. "
                "If web_search fails or returns limited evidence, you may give a best guess only if you state that the answer is uncertain."
            ),
            summary="The task needs time-sensitive external evidence, but the agent has not completed a web_search step.",
            obligation_results=[
                ObligationResult(
                    obligation_id="external_fact_verified",
                    status="missing_evidence",
                    why="no successful web_search result is available",
                )
            ],
        )
    if packet.web_search_evidence_quality == "weak" and not response_has_uncertainty:
        if response_has_blocker:
            return VerificationResult(
                turn_verdict="blocked",
                reason_codes=["weak_external_evidence"],
                next_action_prompt="",
                summary="The available external evidence is too weak to safely finalize a confident answer.",
                obligation_results=[
                    ObligationResult(
                        obligation_id="external_fact_verified",
                        status="blocked",
                        why="available web_search evidence quality is weak",
                    )
                ],
            )
        return VerificationResult(
            turn_verdict="continue",
            reason_codes=["weak_external_evidence"],
            next_action_prompt=(
                "Your available web_search evidence is weak. "
                "You may provide a best guess, but you must explicitly say that the evidence is limited and the answer is uncertain."
            ),
            summary="The available external evidence is weak, but the final answer does not communicate uncertainty.",
            obligation_results=[
                ObligationResult(
                    obligation_id="external_fact_verified",
                    status="missing_evidence",
                    why="available web_search evidence quality is weak",
                )
            ],
        )
    return None


def _evaluate_dependency_install(
    packet: VerificationPacket,
    *,
    response_has_blocker: bool,
) -> VerificationResult | None:
    if packet.verification_state == "conflicting":
        verdict = "blocked" if response_has_blocker else "repair"
        prompt = ""
        if verdict == "repair":
            prompt = (
                "Your current evidence does not prove the dependency is installed in the target environment. "
                "Do not claim the install is complete yet. If installation is still required, use bash for the install "
                "and then verify in the target environment with run_test."
            )
        return VerificationResult(
            turn_verdict=verdict,
            reason_codes=["missing_verification"] if verdict == "blocked" else ["missing_verification", "wrong_tool_choice"],
            next_action_prompt=prompt,
            summary="The dependency-install evidence is conflicting or ran in the wrong environment.",
            obligation_results=[
                ObligationResult(
                    obligation_id="dependency_installed_in_target_environment",
                    status="conflicting_evidence",
                    why="the latest verification ran in the wrong environment or failed",
                )
            ],
        )
    if packet.verification_state == "sufficient":
        return VerificationResult(
            turn_verdict="blocked" if response_has_blocker else "done",
            reason_codes=[],
            next_action_prompt="",
            summary=(
                "The dependency appears verified, but the final answer is still blocked on the reported issue."
                if response_has_blocker
                else "The dependency-install task has sufficient target-environment verification."
            ),
            obligation_results=[
                ObligationResult(
                    obligation_id="dependency_installed_in_target_environment",
                    status="satisfied",
                    why="the dependency was verified in the target environment",
                )
            ],
        )
    return VerificationResult(
        turn_verdict="blocked" if response_has_blocker else "continue",
        reason_codes=["missing_verification"] if response_has_blocker else ["missing_verification", "wrong_tool_choice"],
        next_action_prompt="" if response_has_blocker else (
            "The user asked you to install or set up a dependency, but you have not yet proven it works in the target environment. "
            "Editing dependency files alone is not enough. If installation is required, use bash for the install and then verify in the target environment with run_test."
        ),
        summary=(
            "The dependency-install task is blocked before target-environment verification completed."
            if response_has_blocker
            else "The dependency-install task lacks sufficient target-environment verification."
        ),
        obligation_results=[
            ObligationResult(
                obligation_id="dependency_verified_in_target_environment",
                status="blocked" if response_has_blocker else "missing_evidence",
                why="target-environment verification did not complete",
            )
        ],
    )


def _evaluate_code_change(
    packet: VerificationPacket,
    *,
    response_has_blocker: bool,
) -> VerificationResult | None:
    if packet.has_successful_write_tool:
        if packet.verification_state == "sufficient":
            return VerificationResult(
                turn_verdict="blocked" if response_has_blocker else "done",
                reason_codes=[],
                next_action_prompt="",
                summary=(
                    "The code change was made and a verification attempt occurred, but the final answer is blocked on the reported issue."
                    if response_has_blocker
                    else "The task changed files and attempted verification."
                ),
                obligation_results=[
                    ObligationResult(
                        obligation_id="requested_code_change_verified",
                        status="satisfied",
                        why="strong verification evidence is available",
                    )
                ],
            )
        if packet.verification_state == "conflicting":
            return VerificationResult(
                turn_verdict="blocked" if response_has_blocker else "repair",
                reason_codes=["missing_verification"],
                next_action_prompt="" if response_has_blocker else (
                    "Your latest verification evidence does not support the claimed result. Fix the change or rerun verification in the correct environment before you finalize."
                ),
                summary="The latest verification evidence does not support the claimed code change.",
                obligation_results=[
                    ObligationResult(
                        obligation_id="requested_code_change_verified",
                        status="conflicting_evidence",
                        why="the latest verification failed or ran in the wrong environment",
                    )
                ],
            )
        if packet.run_test_attempt_count >= 3:
            return VerificationResult(
                turn_verdict="blocked",
                reason_codes=["missing_verification", "verification_stalled"],
                next_action_prompt="",
                summary=(
                    "The task kept repeating verification attempts without producing strong evidence. "
                    "Stop this turn and either use a stronger runtime check next or report the exact verification blocker to the user."
                ),
                obligation_results=[
                    ObligationResult(
                        obligation_id="requested_code_change_verified",
                        status="blocked",
                        why="repeated run_test attempts did not produce strong verification evidence",
                    )
                ],
            )
        return VerificationResult(
            turn_verdict="blocked" if response_has_blocker else "continue",
            reason_codes=["missing_verification"],
            next_action_prompt="" if response_has_blocker else (
                "You already changed files for this task, but you have not verified the change yet. Use run_test now unless you are concretely blocked."
            ),
            summary=(
                "The code change was made, but verification is blocked by the reported issue."
                if response_has_blocker
                else "The task changed files but has not been verified yet."
            ),
            obligation_results=[
                ObligationResult(
                    obligation_id="requested_code_change_verified",
                    status="blocked" if response_has_blocker else "missing_evidence",
                    why="strong verification evidence is not yet available",
                )
            ],
        )
    if response_has_blocker:
        return VerificationResult(
            turn_verdict="blocked",
            reason_codes=["missing_edit"],
            next_action_prompt="",
            summary="The requested code change could not be completed because of the reported blocker.",
            obligation_results=[
                ObligationResult(
                    obligation_id="requested_code_change_applied",
                    status="blocked",
                    why="no successful write action was recorded before the blocker",
                )
            ],
        )
    inspected = packet.inspected_tool_names
    if inspected & {"search_text", "read_file", "read_file_range", "list_files", "show_diff", "show_status"}:
        return VerificationResult(
            turn_verdict="continue",
            reason_codes=["missing_edit", "wrong_tool_choice"],
            next_action_prompt=(
                "You have already inspected enough context for a code-change task. Take the next concrete action now: make the minimal edit with apply_patch or edit_file, then continue toward verification."
            ),
            summary="The task requires a code change, but the agent only inspected context and still needs to edit files.",
            obligation_results=[
                ObligationResult(
                    obligation_id="requested_code_change_applied",
                    status="missing_evidence",
                    why="the agent only inspected context and did not change files",
                )
            ],
        )
    return VerificationResult(
        turn_verdict="continue",
        reason_codes=["missing_edit"],
        next_action_prompt=(
            "The user asked for a code change, but you have not changed any files yet. Start by locating the target with search_text or read_file_range if needed, then make the minimal required edit now. After editing, verify with run_test when appropriate, or explain the exact blocker if you cannot proceed."
        ),
        summary="The task requires a code change, but no file edit has been made yet.",
        obligation_results=[
            ObligationResult(
                obligation_id="requested_code_change_applied",
                status="missing_evidence",
                why="no successful write action has been recorded yet",
            )
        ],
    )
