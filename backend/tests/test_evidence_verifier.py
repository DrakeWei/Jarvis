from __future__ import annotations

from unittest import TestCase

import app.services.evidence_verifier as evidence_verifier
import app.services.task_profile_service as task_profile_service
import app.services.verification_packet_service as verification_packet_service


class EvidenceVerifierTests(TestCase):
    def test_build_task_profile_marks_read_only_analysis_as_soft_verification(self) -> None:
        profile = task_profile_service.build_task_profile(
            latest_request="总结一下这个仓库结构",
            requires_code_change=False,
            requires_dependency_install=False,
            requires_external_fact_lookup=False,
            requires_read_only_analysis=True,
        )

        self.assertEqual(profile.verify_level, "soft")
        self.assertEqual(profile.completion_mode, "evidence_check")
        self.assertIn("read_only_analysis", profile.task_kinds)

    def test_build_task_profile_marks_dependency_install_as_hard_verification(self) -> None:
        profile = task_profile_service.build_task_profile(
            latest_request="那你直接用pip install帮我安装吧",
            requires_code_change=False,
            requires_dependency_install=True,
            requires_external_fact_lookup=False,
        )

        self.assertEqual(profile.verify_level, "hard")
        self.assertEqual(profile.completion_mode, "goal_driven")
        self.assertEqual(profile.risk_level, "high")
        self.assertIn("dependency_install", profile.task_kinds)

    def test_build_verification_packet_detects_conflicting_wrong_environment_verification(self) -> None:
        profile = task_profile_service.build_task_profile(
            latest_request="install dependency",
            requires_code_change=False,
            requires_dependency_install=True,
            requires_external_fact_lookup=False,
        )
        packet = verification_packet_service.build_verification_packet(
            task_profile=profile,
            latest_request="install dependency",
            tool_results=[
                verification_packet_service.ToolResultEvidence(
                    tool_name="run_test",
                    status="completed",
                    content="exit_code=0",
                    payload={
                        "classification": "verification",
                        "verification_kind": "package_probe",
                        "evidence_strength": "sufficient",
                        "wrong_environment": True,
                    },
                )
            ],
            web_search_evidence_quality=None,
        )

        self.assertEqual(packet.verification_state, "conflicting")

    def test_build_verification_packet_extracts_missing_python_module_blocker(self) -> None:
        profile = task_profile_service.build_task_profile(
            latest_request="实现一个脚本并验证能运行",
            requires_code_change=True,
            requires_dependency_install=False,
            requires_external_fact_lookup=False,
        )
        packet = verification_packet_service.build_verification_packet(
            task_profile=profile,
            latest_request="实现一个脚本并验证能运行",
            tool_results=[
                verification_packet_service.ToolResultEvidence(
                    tool_name="run_test",
                    status="error",
                    content="exit_code=1\nTraceback...\nModuleNotFoundError: No module named 'numpy'",
                    payload={
                        "classification": "verification",
                        "verification_kind": "script_run",
                        "evidence_strength": "sufficient",
                        "wrong_environment": False,
                        "resolved_command": ["python3", "simple_mnist.py", "--epochs", "1"],
                    },
                )
            ],
            web_search_evidence_quality=None,
        )

        self.assertEqual(packet.blockers, ["run_test: exit_code=1 Traceback... ModuleNotFoundError: No module named 'numpy'"])
        self.assertEqual(packet.repairable_blockers[0].kind, "missing_python_module")
        self.assertEqual(packet.repairable_blockers[0].subject, "numpy")
        self.assertEqual(packet.last_failed_action, "run_test")
        self.assertEqual(packet.last_failed_verification_command, ["python3", "simple_mnist.py", "--epochs", "1"])

    def test_evaluate_packet_requests_repair_for_wrong_environment_dependency_claim(self) -> None:
        profile = task_profile_service.build_task_profile(
            latest_request="安装依赖",
            requires_code_change=False,
            requires_dependency_install=True,
            requires_external_fact_lookup=False,
        )
        packet = verification_packet_service.build_verification_packet(
            task_profile=profile,
            latest_request="安装依赖",
            tool_results=[
                verification_packet_service.ToolResultEvidence(
                    tool_name="run_test",
                    status="completed",
                    content="exit_code=0",
                    payload={
                        "classification": "verification",
                        "verification_kind": "package_probe",
                        "evidence_strength": "sufficient",
                        "wrong_environment": True,
                    },
                )
            ],
            web_search_evidence_quality=None,
        )

        result = evidence_verifier.evaluate_packet(
            packet,
            final_text="已经装好了。",
            response_has_blocker=False,
            response_has_uncertainty=False,
        )

        self.assertEqual(result.turn_verdict, "repair")
        self.assertIn("wrong_tool_choice", result.reason_codes)

    def test_evaluate_packet_treats_weak_syntax_check_as_missing_code_verification(self) -> None:
        profile = task_profile_service.build_task_profile(
            latest_request="Fix the bug in foo.py",
            requires_code_change=True,
            requires_dependency_install=False,
            requires_external_fact_lookup=False,
        )
        packet = verification_packet_service.build_verification_packet(
            task_profile=profile,
            latest_request="Fix the bug in foo.py",
            tool_results=[
                verification_packet_service.ToolResultEvidence(
                    tool_name="edit_file",
                    status="completed",
                    content="Edited foo.py",
                    payload=None,
                ),
                verification_packet_service.ToolResultEvidence(
                    tool_name="run_test",
                    status="completed",
                    content="exit_code=0",
                    payload={
                        "classification": "verification",
                        "verification_kind": "syntax_check",
                        "evidence_strength": "weak",
                        "wrong_environment": False,
                    },
                ),
            ],
            web_search_evidence_quality=None,
        )

        result = evidence_verifier.evaluate_packet(
            packet,
            final_text="I fixed it.",
            response_has_blocker=False,
            response_has_uncertainty=False,
        )

        self.assertEqual(result.turn_verdict, "continue")
        self.assertIn("missing_verification", result.reason_codes)

    def test_evaluate_packet_blocks_repeated_verification_stall(self) -> None:
        profile = task_profile_service.build_task_profile(
            latest_request="Fix the bug in foo.py",
            requires_code_change=True,
            requires_dependency_install=False,
            requires_external_fact_lookup=False,
        )
        packet = verification_packet_service.build_verification_packet(
            task_profile=profile,
            latest_request="Fix the bug in foo.py",
            tool_results=[
                verification_packet_service.ToolResultEvidence(
                    tool_name="edit_file",
                    status="completed",
                    content="Edited foo.py",
                    payload=None,
                ),
                verification_packet_service.ToolResultEvidence(
                    tool_name="run_test",
                    status="completed",
                    content="exit_code=0",
                    payload={
                        "classification": "verification",
                        "verification_kind": "syntax_check",
                        "evidence_strength": "weak",
                        "wrong_environment": False,
                    },
                ),
                verification_packet_service.ToolResultEvidence(
                    tool_name="run_test",
                    status="completed",
                    content="exit_code=0",
                    payload={
                        "classification": "verification",
                        "verification_kind": "syntax_check",
                        "evidence_strength": "weak",
                        "wrong_environment": False,
                    },
                ),
                verification_packet_service.ToolResultEvidence(
                    tool_name="run_test",
                    status="completed",
                    content="exit_code=0",
                    payload={
                        "classification": "verification",
                        "verification_kind": "syntax_check",
                        "evidence_strength": "weak",
                        "wrong_environment": False,
                    },
                ),
            ],
            web_search_evidence_quality=None,
        )

        result = evidence_verifier.evaluate_packet(
            packet,
            final_text="I fixed it.",
            response_has_blocker=False,
            response_has_uncertainty=False,
        )

        self.assertEqual(result.turn_verdict, "blocked")
        self.assertIn("verification_stalled", result.reason_codes)
