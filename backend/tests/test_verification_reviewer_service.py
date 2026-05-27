from __future__ import annotations

from unittest import TestCase
from unittest.mock import patch

import app.services.task_profile_service as task_profile_service
import app.services.verification_packet_service as verification_packet_service
import app.services.verification_reviewer_service as verification_reviewer_service


class VerificationReviewerServiceTests(TestCase):
    def test_build_verification_packet_tracks_original_goal_and_retry_budget(self) -> None:
        profile = task_profile_service.build_task_profile(
            latest_request="Fix the bug in foo.py",
            requires_code_change=True,
            requires_dependency_install=False,
            requires_external_fact_lookup=False,
        )
        packet = verification_packet_service.build_verification_packet(
            task_profile=profile,
            latest_request="继续",
            original_goal="Fix the bug in foo.py",
            current_result_summary="I changed the file.",
            tool_results=[
                verification_packet_service.ToolResultEvidence(
                    tool_name="edit_file",
                    status="completed",
                    content="Edited foo.py",
                )
            ],
            web_search_evidence_quality=None,
            uncertainty_already_stated=False,
            remaining_auto_verify_attempts=1,
        )

        self.assertEqual(packet.original_goal, "Fix the bug in foo.py")
        self.assertEqual(packet.remaining_auto_verify_attempts, 1)
        self.assertIn("the main execution path has not been exercised", packet.open_verification_gaps)

    def test_fallback_reviewer_requests_one_targeted_verification_retry(self) -> None:
        profile = task_profile_service.build_task_profile(
            latest_request="Fix the bug in foo.py",
            requires_code_change=True,
            requires_dependency_install=False,
            requires_external_fact_lookup=False,
        )
        packet = verification_packet_service.build_verification_packet(
            task_profile=profile,
            latest_request="Fix the bug in foo.py",
            original_goal="Fix the bug in foo.py",
            current_result_summary="I fixed the bug.",
            tool_results=[
                verification_packet_service.ToolResultEvidence(
                    tool_name="edit_file",
                    status="completed",
                    content="Edited foo.py",
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
            remaining_auto_verify_attempts=1,
        )

        with patch("app.services.verification_reviewer_service._review_with_llm", return_value=None):
            result = verification_reviewer_service.review_packet(
                packet,
                response_has_blocker=False,
            )

        self.assertEqual(result.verdict, "continue_with_verification")
        self.assertIn("verification_gap", result.reason_codes)
        self.assertIn("run_test", result.next_verification_action or "")

    def test_fallback_reviewer_prefers_script_smoke_check_for_script_tasks(self) -> None:
        profile = task_profile_service.build_task_profile(
            latest_request="实现训练入口，并把命令行参数接到主程序里",
            requires_code_change=True,
            requires_dependency_install=False,
            requires_external_fact_lookup=False,
        )
        packet = verification_packet_service.build_verification_packet(
            task_profile=profile,
            latest_request="实现训练入口，并把命令行参数接到主程序里",
            original_goal="实现训练入口，并把命令行参数接到主程序里",
            current_result_summary="已修改 mnist_simple.py，并通过 py_compile。",
            tool_results=[
                verification_packet_service.ToolResultEvidence(
                    tool_name="write_file",
                    status="completed",
                    content="Wrote 1200 bytes to /tmp/mnist_simple.py",
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
            remaining_auto_verify_attempts=1,
        )

        with patch("app.services.verification_reviewer_service._review_with_llm", return_value=None):
            result = verification_reviewer_service.review_packet(
                packet,
                response_has_blocker=False,
            )

        self.assertEqual(result.verdict, "continue_with_verification")
        self.assertIn("mnist_simple.py", result.next_verification_action or "")
        self.assertIn("--help", result.next_verification_action or "")

    def test_fallback_reviewer_prefers_repair_action_for_missing_python_module(self) -> None:
        profile = task_profile_service.build_task_profile(
            latest_request="在这个目录下用Python实现一个简易的MNIST手写识别体。",
            requires_code_change=True,
            requires_dependency_install=False,
            requires_external_fact_lookup=False,
        )
        packet = verification_packet_service.build_verification_packet(
            task_profile=profile,
            latest_request="在这个目录下用Python实现一个简易的MNIST手写识别体。",
            original_goal="在这个目录下用Python实现一个简易的MNIST手写识别体。",
            current_result_summary="已创建 simple_mnist.py，但冒烟运行失败。",
            tool_results=[
                verification_packet_service.ToolResultEvidence(
                    tool_name="write_file",
                    status="completed",
                    content="Wrote 1200 bytes to /tmp/simple_mnist.py",
                ),
                verification_packet_service.ToolResultEvidence(
                    tool_name="run_test",
                    status="error",
                    content="exit_code=1\nModuleNotFoundError: No module named 'numpy'",
                    payload={
                        "classification": "verification",
                        "verification_kind": "script_run",
                        "evidence_strength": "sufficient",
                        "wrong_environment": False,
                        "resolved_command": ["python3", "simple_mnist.py", "--epochs", "1"],
                    },
                ),
            ],
            web_search_evidence_quality=None,
            remaining_repair_attempts=1,
            remaining_auto_verify_attempts=1,
        )

        with patch("app.services.verification_reviewer_service._review_with_llm", return_value=None):
            result = verification_reviewer_service.review_packet(
                packet,
                response_has_blocker=False,
            )

        self.assertEqual(result.verdict, "continue_with_repair")
        self.assertEqual(result.next_phase, "repair")
        self.assertIn("Use bash to install `numpy`", result.next_verification_action or "")
        self.assertIn("['python3', 'simple_mnist.py', '--epochs', '1']", result.next_verification_action or "")

    def test_fallback_reviewer_prefers_target_python_for_missing_module_install(self) -> None:
        profile = task_profile_service.build_task_profile(
            latest_request="安装一下缺失依赖，然后把脚本跑起来",
            requires_code_change=True,
            requires_dependency_install=False,
            requires_external_fact_lookup=False,
        )
        packet = verification_packet_service.build_verification_packet(
            task_profile=profile,
            latest_request="安装一下缺失依赖，然后把脚本跑起来",
            original_goal="安装一下缺失依赖，然后把脚本跑起来",
            current_result_summary="脚本已写好，但当前解释器缺少 pypdf。",
            tool_results=[
                verification_packet_service.ToolResultEvidence(
                    tool_name="write_file",
                    status="completed",
                    content="Wrote 2990 bytes to /tmp/pdf_batch_tool.py",
                ),
                verification_packet_service.ToolResultEvidence(
                    tool_name="run_test",
                    status="error",
                    content="exit_code=1\nModuleNotFoundError: No module named 'pypdf'",
                    payload={
                        "classification": "verification",
                        "verification_kind": "script_run",
                        "evidence_strength": "sufficient",
                        "wrong_environment": False,
                        "resolved_command": ["/tmp/workspace/.venv/bin/python", "pdf_batch_tool.py", "--help"],
                    },
                ),
            ],
            web_search_evidence_quality=None,
            remaining_repair_attempts=1,
            remaining_auto_verify_attempts=1,
        )

        with patch("app.services.verification_reviewer_service._review_with_llm", return_value=None):
            result = verification_reviewer_service.review_packet(
                packet,
                response_has_blocker=False,
            )

        self.assertEqual(result.verdict, "continue_with_repair")
        self.assertIn("/tmp/workspace/.venv/bin/python -m pip install pypdf", result.next_verification_action or "")
        self.assertIn("['/tmp/workspace/.venv/bin/python', 'pdf_batch_tool.py', '--help']", result.next_verification_action or "")

    def test_fallback_reviewer_blocks_when_weak_verification_is_stalled(self) -> None:
        profile = task_profile_service.build_task_profile(
            latest_request="Fix the bug in foo.py",
            requires_code_change=True,
            requires_dependency_install=False,
            requires_external_fact_lookup=False,
        )
        packet = verification_packet_service.build_verification_packet(
            task_profile=profile,
            latest_request="Fix the bug in foo.py",
            original_goal="Fix the bug in foo.py",
            current_result_summary="I fixed the bug.",
            tool_results=[
                verification_packet_service.ToolResultEvidence(
                    tool_name="edit_file",
                    status="completed",
                    content="Edited foo.py",
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
            remaining_auto_verify_attempts=0,
        )

        with patch("app.services.verification_reviewer_service._review_with_llm", return_value=None):
            result = verification_reviewer_service.review_packet(
                packet,
                response_has_blocker=False,
            )

        self.assertEqual(result.verdict, "blocked_uncertain")
        self.assertIn("verification_stalled", result.reason_codes)
        self.assertIn("Remaining proof gap", result.user_visible_uncertainty or "")
