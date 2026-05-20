from __future__ import annotations

import argparse
import asyncio
from pathlib import Path

from app.main import initialize_runtime_for_role
from app.services.runtime_state import runtime
from evals.runner import EvalRunner, load_task_dir, write_suite_report
from evals.runtime_adapter import RuntimeManagerEvalAdapter


def _default_report_path() -> Path:
    return Path("backend/evals/reports/latest-report.json")


def _select_tasks(task_dir: Path, task_ids: set[str], tags: set[str]):
    tasks = load_task_dir(task_dir)
    if task_ids:
        tasks = [task for task in tasks if task.id in task_ids]
    if tags:
        tasks = [task for task in tasks if set(task.tags) & tags]
    return tasks


async def run_cli(args) -> int:
    await initialize_runtime_for_role()
    task_dir = Path(args.task_dir)
    tasks = _select_tasks(task_dir, set(args.task_id), set(args.tag))
    if not tasks:
        print(f"No benchmark tasks matched under {task_dir}.")
        return 1

    adapter = RuntimeManagerEvalAdapter(runtime)
    runner = EvalRunner(adapter, poll_interval_seconds=args.poll_interval_seconds)
    report = await runner.run_tasks(tasks)
    report_path = write_suite_report(report, Path(args.report_path))

    if not args.keep_sessions:
        for result in report.results:
            if result.session_id:
                await runtime.soft_delete_session(result.session_id)

    print(f"Tasks: {report.total_tasks}")
    print(f"Labels: {dict(report.counts_by_label)}")
    if report.counts_by_failure_tag:
        print(f"Failure tags: {dict(report.counts_by_failure_tag)}")
    if report.counts_by_tag_and_label:
        print(f"Tag label counts: {dict(report.counts_by_tag_and_label)}")
    print(f"Report: {report_path}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run Jarvis benchmark tasks and write a JSON report.")
    parser.add_argument("--task-dir", default="backend/evals/tasks/smoke", help="Task directory containing task JSON specs.")
    parser.add_argument("--report-path", default=str(_default_report_path()), help="Where to write the JSON report.")
    parser.add_argument("--task-id", action="append", default=[], help="Run only matching task id. Can be passed multiple times.")
    parser.add_argument("--tag", action="append", default=[], help="Run only tasks containing at least one matching tag.")
    parser.add_argument("--keep-sessions", action="store_true", help="Keep benchmark sessions visible instead of soft-deleting them after the run.")
    parser.add_argument("--poll-interval-seconds", type=float, default=0.25, help="Polling interval while waiting for terminal turn state.")
    return parser


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()
    raise SystemExit(asyncio.run(run_cli(args)))
