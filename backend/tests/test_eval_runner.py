from __future__ import annotations

import json
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest import IsolatedAsyncioTestCase, TestCase
from unittest.mock import patch

from app.schemas.approvals import ApprovalSummary
from app.schemas.events import SessionSummary
from app.schemas.tools import ToolExecutionSummary
from app.schemas.turns import TurnSummary
from evals.cli import _select_tasks
from evals.models import BenchmarkCheckSpec, BenchmarkRunEvidence, BenchmarkSuiteReport, BenchmarkTaskSpec
from evals.runner import EvalRunner, evaluate_benchmark_task, load_task_dir, write_suite_report


class EvalModelsTests(TestCase):
    def test_tool_check_requires_tool_name(self) -> None:
        with self.assertRaises(ValueError):
            BenchmarkCheckSpec(kind="tool_used")

    def test_tool_used_any_of_requires_tool_names(self) -> None:
        with self.assertRaises(ValueError):
            BenchmarkCheckSpec(kind="tool_used_any_of")

    def test_load_task_dir_finds_smoke_tasks(self) -> None:
        tasks = load_task_dir(Path("backend/evals/tasks/smoke"))
        self.assertGreaterEqual(len(tasks), 5)
        self.assertTrue(any(task.id == "git-state-summary" for task in tasks))
        self.assertTrue(any(task.id == "fix-calculator-add" for task in tasks))

    def test_load_task_dir_finds_core_tasks(self) -> None:
        tasks = load_task_dir(Path("backend/evals/tasks/core"))
        self.assertGreaterEqual(len(tasks), 8)
        self.assertTrue(any(task.id == "fix-pricing-loyalty-discount" for task in tasks))
        self.assertTrue(any(task.id == "approval-recovery-flow-summary" for task in tasks))

    def test_select_tasks_can_filter_by_tag_and_id(self) -> None:
        selected = _select_tasks(Path("backend/evals/tasks/smoke"), {"git-state-summary"}, {"git"})
        self.assertEqual([task.id for task in selected], ["git-state-summary"])

    def test_failed_tool_expectation_maps_to_wrong_tool_choice(self) -> None:
        task = BenchmarkTaskSpec(
            id="tool-check",
            name="Tool Check",
            user_prompt="Use search_text",
            expected_checks=[BenchmarkCheckSpec(kind="tool_used", tool_name="search_text")],
        )
        evidence = BenchmarkRunEvidence(task_id="tool-check", final_assistant_message="Done")
        result = evaluate_benchmark_task(task, evidence)
        self.assertEqual(result.label, "fail")
        self.assertEqual(result.primary_failure_tag, "wrong_tool_choice")

    def test_tool_used_any_of_can_pass_with_read_file_range(self) -> None:
        task = BenchmarkTaskSpec(
            id="tool-any-of",
            name="Tool Any Of",
            user_prompt="Inspect the runtime manager",
            expected_checks=[BenchmarkCheckSpec(kind="tool_used_any_of", tool_names=["read_file", "read_file_range"])],
        )
        evidence = BenchmarkRunEvidence(
            task_id="tool-any-of",
            tool_executions=[
                ToolExecutionSummary(
                    id=1,
                    session_id="session-1",
                    tool_name="read_file_range",
                    status="completed",
                    input_json="{}",
                    output_text="content",
                    created_at="2026-05-19T00:00:00+00:00",
                )
            ],
        )
        result = evaluate_benchmark_task(task, evidence)
        self.assertEqual(result.label, "pass")

    def test_file_check_failures_map_to_edit_failure(self) -> None:
        with TemporaryDirectory() as tmpdir:
            target = Path(tmpdir) / "sample.txt"
            target.write_text("bad value\n")
            task = BenchmarkTaskSpec(
                id="file-check",
                name="File Check",
                user_prompt="Fix the file",
                expected_checks=[
                    BenchmarkCheckSpec(kind="file_contains_all", path="sample.txt", contains=["good value"]),
                    BenchmarkCheckSpec(kind="workspace_diff_not_empty"),
                ],
            )
            evidence = BenchmarkRunEvidence(
                task_id="file-check",
                effective_workspace_path=tmpdir,
                workspace_diff_is_empty=True,
            )
            result = evaluate_benchmark_task(task, evidence)

        self.assertEqual(result.label, "fail")
        self.assertEqual(result.primary_failure_tag, "edit_failure")

    def test_write_suite_report_writes_json_file(self) -> None:
        task = BenchmarkTaskSpec(
            id="report",
            name="Report",
            user_prompt="Summarize",
            expected_checks=[BenchmarkCheckSpec(kind="final_text_contains_all", contains=["done"])],
        )
        evidence = BenchmarkRunEvidence(task_id="report", final_assistant_message="done")
        result = evaluate_benchmark_task(task, evidence)
        with TemporaryDirectory() as tmpdir:
            report = BenchmarkSuiteReport(
                total_tasks=1,
                counts_by_label={"pass": 1},
                counts_by_failure_tag={},
                results=[result],
            )
            path = write_suite_report(report, Path(tmpdir) / "report.json")
            self.assertTrue(path.exists())
            payload = json.loads(path.read_text())
            self.assertEqual(payload["total_tasks"], 1)
            self.assertEqual(payload["results"][0]["task_id"], "report")

class FakeRuntime:
    def __init__(self) -> None:
        self.session = SessionSummary(
            session_id="session-1",
            title="Eval session",
            workspace_mode="bound",
            canonical_workspace_path="/tmp",
            workspace_label="tmp",
            workspace_fingerprint="workspace-fp",
            repo_root=None,
            git_enabled=False,
            lead_branch=None,
            head_revision=None,
            working_tree_status=None,
            detached_head=False,
            status="idle",
            created_at="2026-05-19T00:00:00+00:00",
            updated_at="2026-05-19T00:00:00+00:00",
        )
        self.decisions: list[tuple[int, bool, str]] = []
        self.turn = TurnSummary(
            id=12,
            session_id="session-1",
            user_message_id=101,
            workspace_path="/tmp",
            workspace_fingerprint="workspace-fp",
            execution_mode="normal",
            status="running",
            started_at="2026-05-19T00:00:00+00:00",
            updated_at="2026-05-19T00:00:00+00:00",
            completed_at=None,
            cancel_requested=False,
            last_checkpoint_seq=0,
            resume_hint=None,
            error_summary=None,
            resumable=False,
        )
        self.approval = ApprovalSummary(
            id=1,
            session_id="session-1",
            approval_type="bash",
            status="pending",
            prompt="bash\nrg TODO backend",
            feedback=None,
            created_at="2026-05-19T00:00:00+00:00",
        )
        self.poll_count = 0

    async def create_session(self, payload):
        return self.session

    async def append_message(self, session_id: str, payload):
        return None

    def get_session_state(self, session_id: str):
        self.poll_count += 1
        if self.poll_count < 2:
            return SimpleNamespace(session=SimpleNamespace(status="running"), active_turn=self.turn)
        return SimpleNamespace(session=SimpleNamespace(status="idle"), active_turn=None)

    def list_timeline(self, session_id: str):
        return []

    def list_tool_executions(self, session_id: str | None = None):
        return [
            ToolExecutionSummary(
                id=1,
                session_id="session-1",
                tool_name="read_file",
                status="completed",
                input_json="{}",
                output_text="content",
                created_at="2026-05-19T00:00:00+00:00",
            )
        ]

    def list_approvals(self, session_id: str | None = None):
        return [self.approval] if self.approval.status == "pending" else []

    def list_turns(self, session_id: str | None = None):
        if self.poll_count < 2:
            return [self.turn]
        completed = self.turn.model_copy(update={"status": "completed", "completed_at": "2026-05-19T00:00:01+00:00"})
        return [completed]

    async def decide_approval(self, approval_id: int, approve: bool, feedback: str = ""):
        self.decisions.append((approval_id, approve, feedback))
        self.approval = self.approval.model_copy(
            update={"status": "approved" if approve else "rejected", "feedback": feedback}
        )
        return self.approval


class EvalRunnerTests(IsolatedAsyncioTestCase):
    async def test_runner_auto_rejects_bash_approval_when_policy_denies_shell(self) -> None:
        runtime = FakeRuntime()
        runner = EvalRunner(runtime, poll_interval_seconds=0.01)
        task = BenchmarkTaskSpec(
            id="deny-shell",
            name="Deny shell",
            user_prompt="Inspect the repo",
            approval_policy={"mode": "deny_all_shell"},
            expected_checks=[
                BenchmarkCheckSpec(kind="final_text_contains_all", contains=["done"]),
                BenchmarkCheckSpec(kind="max_pending_approval_count", max_count=0),
                BenchmarkCheckSpec(kind="terminal_turn_status_in", statuses=["completed"]),
            ],
        )
        with patch("evals.runner.session_service.list_message_records", return_value=[{"role": "assistant", "content": "Done"}]):
            result = await runner.run_task(task)

        self.assertEqual(result.label, "pass")
        self.assertEqual(runtime.decisions, [(1, False, "benchmark policy deny_all_shell")])

    async def test_runner_report_tracks_counts_by_tag_and_label(self) -> None:
        runtime = FakeRuntime()
        runner = EvalRunner(runtime, poll_interval_seconds=0.01)
        task = BenchmarkTaskSpec(
            id="tagged-pass",
            name="Tagged Pass",
            user_prompt="Inspect the repo",
            tags=["smoke", "read_only"],
            approval_policy={"mode": "deny_all_shell"},
            expected_checks=[
                BenchmarkCheckSpec(kind="final_text_contains_all", contains=["done"]),
                BenchmarkCheckSpec(kind="terminal_turn_status_in", statuses=["completed"]),
            ],
        )
        with patch("evals.runner.session_service.list_message_records", return_value=[{"role": "assistant", "content": "Done"}]):
            report = await runner.run_tasks([task])

        self.assertEqual(report.counts_by_tag_and_label["smoke"]["pass"], 1)
        self.assertEqual(report.counts_by_tag_and_label["read_only"]["pass"], 1)
