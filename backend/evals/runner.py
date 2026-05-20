from __future__ import annotations

import asyncio
import json
import re
import shutil
import subprocess
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Protocol

from app.core import workspace as workspace_utils
from app.schemas.events import MessageCreate, SessionCreate, SessionSummary
from app.schemas.session_state import SessionStateSummary
from app.schemas.turns import TurnSummary
from app.services import session_service
from evals.models import (
    ApprovalPolicySpec,
    BenchmarkCheckResult,
    BenchmarkRunEvidence,
    BenchmarkSuiteReport,
    BenchmarkTaskResult,
    BenchmarkTaskSpec,
)


class RuntimeEvalAdapter(Protocol):
    async def create_session(self, payload: SessionCreate) -> SessionSummary: ...

    async def append_message(self, session_id: str, payload: MessageCreate) -> None: ...

    def get_session_state(self, session_id: str) -> SessionStateSummary | None: ...

    def list_timeline(self, session_id: str): ...

    def list_tool_executions(self, session_id: str | None = None): ...

    def list_approvals(self, session_id: str | None = None): ...

    def list_turns(self, session_id: str | None = None): ...

    async def decide_approval(self, approval_id: int, approve: bool, feedback: str = ""): ...


def load_task_file(path: str | Path) -> BenchmarkTaskSpec:
    task_path = Path(path)
    return BenchmarkTaskSpec.model_validate(json.loads(task_path.read_text()))


def load_task_dir(path: str | Path) -> list[BenchmarkTaskSpec]:
    root = Path(path)
    tasks = [load_task_file(task_file) for task_file in sorted(root.rglob("*.json"))]
    return tasks


def write_suite_report(report: BenchmarkSuiteReport, path: str | Path) -> Path:
    report_path = Path(path)
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(report.model_dump_json(indent=2))
    return report_path


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _message_text(content: object) -> str:
    if isinstance(content, str):
        return content
    if not isinstance(content, list):
        return str(content)
    parts: list[str] = []
    for part in content:
        if isinstance(part, dict) and part.get("type") == "text":
            text = str(part.get("text", "")).strip()
            if text:
                parts.append(text)
    return "\n".join(parts)


def _last_assistant_message(session_id: str) -> str:
    messages = session_service.list_message_records(session_id)
    for message in reversed(messages):
        if message.get("role") != "assistant":
            continue
        text = _message_text(message.get("content"))
        if text.strip():
            return text.strip()
    return ""


def _git_output(workspace_path: str, args: list[str]) -> str | None:
    try:
        result = subprocess.run(
            ["git", *args],
            cwd=workspace_path,
            capture_output=True,
            text=True,
            check=False,
            timeout=20,
        )
    except Exception:
        return None
    if result.returncode != 0:
        return None
    output = (result.stdout or result.stderr).strip()
    return output


def _workspace_status_text(session: SessionSummary | None) -> str | None:
    if session is None or not session.git_enabled:
        return None
    return _git_output(session.canonical_workspace_path, ["status", "--short"])


def _workspace_diff_text(session: SessionSummary | None) -> str | None:
    if session is None or not session.git_enabled:
        return None
    output = _git_output(session.canonical_workspace_path, ["diff", "--no-ext-diff", "HEAD", "--"])
    if output is None:
        return None
    return output[:20000]


def _check_result(kind: str, passed: bool, message: str) -> BenchmarkCheckResult:
    return BenchmarkCheckResult(kind=kind, passed=passed, message=message)


def _read_file_from_workspace(workspace_path: str | None, relative_path: str | None) -> str | None:
    if not workspace_path or not relative_path:
        return None
    workspace = Path(workspace_path).resolve()
    candidate = (workspace / relative_path).resolve()
    if not workspace_utils.path_within(workspace, candidate):
        return None
    if not candidate.exists() or not candidate.is_file():
        return None
    try:
        return candidate.read_text()
    except Exception:
        return None


def _evaluate_checks(task: BenchmarkTaskSpec, evidence: BenchmarkRunEvidence) -> list[BenchmarkCheckResult]:
    results: list[BenchmarkCheckResult] = []
    final_text = evidence.final_assistant_message.lower()
    tool_names = [tool.tool_name for tool in evidence.tool_executions]

    for check in task.expected_checks:
        if check.kind == "final_text_contains_all":
            missing = [item for item in check.contains if item.lower() not in final_text]
            results.append(
                _check_result(
                    check.kind,
                    not missing,
                    "all expected substrings found" if not missing else f"missing substrings: {', '.join(missing)}",
                )
            )
            continue
        if check.kind == "final_text_not_contains_any":
            present = [item for item in check.excludes if item.lower() in final_text]
            results.append(
                _check_result(
                    check.kind,
                    not present,
                    "no forbidden substrings found" if not present else f"forbidden substrings present: {', '.join(present)}",
                )
            )
            continue
        if check.kind == "file_contains_all":
            file_text = (_read_file_from_workspace(evidence.effective_workspace_path, check.path) or "").lower()
            missing = [item for item in check.contains if item.lower() not in file_text]
            results.append(
                _check_result(
                    check.kind,
                    not missing,
                    "all expected file substrings found" if not missing else f"missing file substrings: {', '.join(missing)}",
                )
            )
            continue
        if check.kind == "file_not_contains_any":
            file_text = (_read_file_from_workspace(evidence.effective_workspace_path, check.path) or "").lower()
            present = [item for item in check.excludes if item.lower() in file_text]
            results.append(
                _check_result(
                    check.kind,
                    not present,
                    "no forbidden file substrings found" if not present else f"forbidden file substrings present: {', '.join(present)}",
                )
            )
            continue
        if check.kind == "max_total_approval_count":
            passed = len(evidence.approvals) <= int(check.max_count or 0)
            results.append(_check_result(check.kind, passed, f"approval count={len(evidence.approvals)}"))
            continue
        if check.kind == "max_pending_approval_count":
            passed = evidence.pending_approval_count <= int(check.max_count or 0)
            results.append(_check_result(check.kind, passed, f"pending approvals={evidence.pending_approval_count}"))
            continue
        if check.kind == "tool_used":
            passed = check.tool_name in tool_names
            results.append(_check_result(check.kind, passed, f"tool_used={check.tool_name}"))
            continue
        if check.kind == "tool_used_any_of":
            passed = any(tool_name in tool_names for tool_name in check.tool_names)
            results.append(_check_result(check.kind, passed, f"tool_used_any_of={','.join(check.tool_names)}"))
            continue
        if check.kind == "tool_not_used":
            passed = check.tool_name not in tool_names
            results.append(_check_result(check.kind, passed, f"tool_not_used={check.tool_name}"))
            continue
        if check.kind == "terminal_turn_status_in":
            passed = (evidence.terminal_turn_status or "") in check.statuses
            results.append(
                _check_result(check.kind, passed, f"terminal_turn_status={evidence.terminal_turn_status or '(none)'}")
            )
            continue
        if check.kind == "session_status_in":
            passed = (evidence.session_status or "") in check.statuses
            results.append(_check_result(check.kind, passed, f"session_status={evidence.session_status or '(none)'}"))
            continue
        if check.kind == "workspace_diff_is_empty":
            passed = evidence.workspace_diff_is_empty is True
            results.append(_check_result(check.kind, passed, "workspace diff is empty" if passed else "workspace diff is not empty"))
            continue
        if check.kind == "workspace_diff_not_empty":
            passed = evidence.workspace_diff_is_empty is False
            results.append(_check_result(check.kind, passed, "workspace diff is not empty" if passed else "workspace diff is empty"))
            continue
    return results


def _infer_failure_tag(evidence: BenchmarkRunEvidence, checks: list[BenchmarkCheckResult]) -> str | None:
    if evidence.run_error:
        return "runtime_recovery"
    failed_kinds = {result.kind for result in checks if not result.passed}
    failed_messages = [result.message for result in checks if not result.passed]
    if evidence.timed_out and evidence.pending_approval_count > 0:
        return "approval_stall"
    if "max_total_approval_count" in failed_kinds or "max_pending_approval_count" in failed_kinds:
        return "approval_stall"
    if "workspace_diff_is_empty" in failed_kinds:
        return "workspace_violation"
    if "workspace_diff_not_empty" in failed_kinds:
        return "edit_failure"
    if "file_contains_all" in failed_kinds or "file_not_contains_any" in failed_kinds:
        return "edit_failure"
    if any(
        "tool_used=run_test" in message
        or "tool_not_used=run_test" in message
        or "tool_used_any_of=run_test" in message
        for message in failed_messages
    ):
        return "verification_missing"
    if "tool_used" in failed_kinds or "tool_not_used" in failed_kinds:
        return "wrong_tool_choice"
    if "terminal_turn_status_in" in failed_kinds or "session_status_in" in failed_kinds:
        return "runtime_recovery"
    if "final_text_contains_all" in failed_kinds or "final_text_not_contains_any" in failed_kinds:
        return "task_understanding"
    if any(tool.tool_name == "run_subagent" and tool.status != "completed" for tool in evidence.tool_executions):
        return "subagent_coordination"
    combined = " ".join(
        bit.lower()
        for bit in [
            evidence.latest_turn_error_summary or "",
            evidence.latest_turn_resume_hint or "",
            evidence.final_assistant_message or "",
        ]
    )
    if "iteration" in combined:
        return "iteration_exhausted"
    if "workspace" in combined:
        return "workspace_violation"
    return "runtime_recovery"


def evaluate_benchmark_task(task: BenchmarkTaskSpec, evidence: BenchmarkRunEvidence) -> BenchmarkTaskResult:
    checks = _evaluate_checks(task, evidence)
    if evidence.run_error:
        label = "invalid_run"
    elif not checks:
        label = "invalid_run"
    elif all(result.passed for result in checks):
        label = "pass"
    elif any(result.passed for result in checks):
        label = "partial"
    else:
        label = "fail"
    primary_failure_tag = None if label == "pass" else _infer_failure_tag(evidence, checks)
    return BenchmarkTaskResult(
        task_id=task.id,
        task_name=task.name,
        label=label,
        primary_failure_tag=primary_failure_tag,
        timed_out=evidence.timed_out,
        duration_seconds=evidence.duration_seconds,
        session_id=evidence.session_id,
        checks=checks,
        evidence=evidence,
    )


def _prepare_workspace(task: BenchmarkTaskSpec) -> tuple[str | None, Path | None]:
    if task.workspace_fixture:
        fixture_source = Path(task.workspace_fixture).resolve()
        if not fixture_source.exists() or not fixture_source.is_dir():
            raise ValueError(f"Workspace fixture does not exist or is not a directory: {task.workspace_fixture}")
        temp_root = Path(tempfile.mkdtemp(prefix=f"jarvis-eval-{task.id}-"))
        destination = temp_root / fixture_source.name
        shutil.copytree(fixture_source, destination)
        if task.initialize_git:
            _initialize_git_repo(destination)
        return destination.as_posix(), temp_root
    return task.workspace_path, None


def _initialize_git_repo(workspace: Path) -> None:
    subprocess.run(["git", "init", "-q"], cwd=workspace, check=True, capture_output=True, text=True)
    subprocess.run(["git", "config", "user.email", "eval@example.com"], cwd=workspace, check=True, capture_output=True, text=True)
    subprocess.run(["git", "config", "user.name", "Jarvis Eval"], cwd=workspace, check=True, capture_output=True, text=True)
    subprocess.run(["git", "add", "."], cwd=workspace, check=True, capture_output=True, text=True)
    commit = subprocess.run(
        ["git", "commit", "-q", "-m", "fixture baseline"],
        cwd=workspace,
        capture_output=True,
        text=True,
        check=False,
    )
    if commit.returncode != 0:
        stderr = (commit.stderr or "").lower()
        stdout = (commit.stdout or "").lower()
        if "nothing to commit" not in stderr and "nothing to commit" not in stdout:
            raise RuntimeError((commit.stdout or commit.stderr).strip() or "Unable to create fixture baseline commit.")


class EvalRunner:
    def __init__(self, runtime: RuntimeEvalAdapter, *, poll_interval_seconds: float = 0.25) -> None:
        self.runtime = runtime
        self.poll_interval_seconds = max(0.05, poll_interval_seconds)

    async def run_task(self, task: BenchmarkTaskSpec) -> BenchmarkTaskResult:
        started = time.monotonic()
        started_at = _now_iso()
        session_summary: SessionSummary | None = None
        effective_workspace_path: str | None = None
        cleanup_root: Path | None = None
        result: BenchmarkTaskResult | None = None
        try:
            effective_workspace_path, cleanup_root = _prepare_workspace(task)
            session_summary = await self.runtime.create_session(
                SessionCreate(
                    title=f"Eval {task.id}",
                    workspace_path=effective_workspace_path,
                    workspace_mode=task.workspace_mode,
                )
            )
            await self.runtime.append_message(
                session_summary.session_id,
                MessageCreate(
                    role="user",
                    content=task.user_prompt,
                    execution_mode=task.execution_mode,
                ),
            )
            evidence = await self._wait_for_completion(task, session_summary, effective_workspace_path=effective_workspace_path)
            result = evaluate_benchmark_task(task, evidence)
        except Exception as exc:
            evidence = BenchmarkRunEvidence(
                task_id=task.id,
                session_id=session_summary.session_id if session_summary else None,
                effective_workspace_path=effective_workspace_path,
                started_at=started_at,
                finished_at=_now_iso(),
                duration_seconds=max(0.0, time.monotonic() - started),
                run_error=str(exc),
            )
            result = evaluate_benchmark_task(task, evidence)
        finally:
            if cleanup_root is not None:
                shutil.rmtree(cleanup_root, ignore_errors=True)
        return result if result is not None else evaluate_benchmark_task(task, evidence)

    async def run_tasks(self, tasks: list[BenchmarkTaskSpec]) -> BenchmarkSuiteReport:
        started = time.monotonic()
        started_at = _now_iso()
        results: list[BenchmarkTaskResult] = []
        task_tags_by_id: dict[str, list[str]] = {}
        for task in tasks:
            task_tags_by_id[task.id] = list(task.tags)
            results.append(await self.run_task(task))
        counts_by_label: dict[str, int] = {}
        counts_by_failure_tag: dict[str, int] = {}
        counts_by_tag_and_label: dict[str, dict[str, int]] = {}
        for result in results:
            counts_by_label[result.label] = counts_by_label.get(result.label, 0) + 1
            if result.primary_failure_tag:
                counts_by_failure_tag[result.primary_failure_tag] = counts_by_failure_tag.get(result.primary_failure_tag, 0) + 1
            for tag in task_tags_by_id.get(result.task_id, []):
                tag_counts = counts_by_tag_and_label.setdefault(tag, {})
                tag_counts[result.label] = tag_counts.get(result.label, 0) + 1
        return BenchmarkSuiteReport(
            started_at=started_at,
            finished_at=_now_iso(),
            duration_seconds=max(0.0, time.monotonic() - started),
            total_tasks=len(results),
            counts_by_label=counts_by_label,
            counts_by_failure_tag=counts_by_failure_tag,
            counts_by_tag_and_label=counts_by_tag_and_label,
            results=results,
        )

    async def _wait_for_completion(
        self,
        task: BenchmarkTaskSpec,
        session_summary: SessionSummary,
        *,
        effective_workspace_path: str | None,
    ) -> BenchmarkRunEvidence:
        started = time.monotonic()
        started_at = _now_iso()
        deadline = started + task.time_budget_seconds
        initial_diff_text = _workspace_diff_text(session_summary)
        initial_status_text = _workspace_status_text(session_summary)

        while True:
            approvals = list(self.runtime.list_approvals(session_summary.session_id))
            await self._resolve_pending_approvals(task.approval_policy, approvals)
            state = self.runtime.get_session_state(session_summary.session_id)
            turns = list(self.runtime.list_turns(session_summary.session_id))
            latest_turn = turns[0] if turns else None
            if self._is_terminal(state, approvals, latest_turn):
                break
            if time.monotonic() >= deadline:
                break
            await asyncio.sleep(self.poll_interval_seconds)

        approvals = list(self.runtime.list_approvals(session_summary.session_id))
        state = self.runtime.get_session_state(session_summary.session_id)
        turns = list(self.runtime.list_turns(session_summary.session_id))
        latest_turn = turns[0] if turns else None
        timeline_events = list(self.runtime.list_timeline(session_summary.session_id))
        tool_executions = list(self.runtime.list_tool_executions(session_summary.session_id))
        diff_text = _workspace_diff_text(session_summary)
        status_text = _workspace_status_text(session_summary)
        workspace_changed = (diff_text != initial_diff_text) or (status_text != initial_status_text)
        return BenchmarkRunEvidence(
            task_id=task.id,
            session_id=session_summary.session_id,
            effective_workspace_path=effective_workspace_path,
            started_at=started_at,
            finished_at=_now_iso(),
            duration_seconds=max(0.0, time.monotonic() - started),
            timed_out=time.monotonic() >= deadline and not self._is_terminal(state, approvals, latest_turn),
            session_status=(state.session.status if state else session_summary.status),
            terminal_turn_status=(latest_turn.status if latest_turn else None),
            latest_turn_error_summary=(latest_turn.error_summary if latest_turn else None),
            latest_turn_resume_hint=(latest_turn.resume_hint if latest_turn else None),
            final_assistant_message=_last_assistant_message(session_summary.session_id),
            pending_approval_count=sum(1 for approval in approvals if approval.status == "pending"),
            initial_workspace_status_text=initial_status_text,
            initial_workspace_diff_text=initial_diff_text,
            workspace_status_text=status_text,
            workspace_diff_text=diff_text,
            workspace_diff_is_empty=(not workspace_changed if diff_text is not None or status_text is not None else None),
            approvals=approvals,
            timeline_events=timeline_events,
            tool_executions=tool_executions,
            turns=turns,
        )

    def _is_terminal(
        self,
        state: SessionStateSummary | None,
        approvals,
        latest_turn: TurnSummary | None,
    ) -> bool:
        if any(approval.status == "pending" for approval in approvals):
            return False
        if state and state.active_turn is not None:
            return False
        if latest_turn is None:
            return False
        return latest_turn.status in {"completed", "cancelled", "failed", "interrupted"}

    async def _resolve_pending_approvals(self, policy: ApprovalPolicySpec, approvals) -> None:
        for approval in approvals:
            if approval.status != "pending":
                continue
            decision = self._decision_for_approval(policy, approval.approval_type, approval.prompt)
            if decision is None:
                continue
            approve, feedback = decision
            await self.runtime.decide_approval(approval.id, approve=approve, feedback=feedback)

    def _decision_for_approval(
        self,
        policy: ApprovalPolicySpec,
        approval_type: str,
        prompt: str,
    ) -> tuple[bool, str] | None:
        if approval_type != "bash":
            return None
        feedback = policy.feedback_message or f"benchmark policy {policy.mode}"
        if policy.mode == "manual_required":
            return None
        if policy.mode == "deny_all_shell":
            return False, feedback
        if policy.mode == "allow_shell":
            return True, feedback
        if policy.mode == "allow_shell_if_pattern_matches":
            normalized = prompt.lower()
            for pattern in policy.allow_patterns:
                if re.search(pattern, normalized, flags=re.IGNORECASE):
                    return True, feedback
            return False, feedback
        return None
