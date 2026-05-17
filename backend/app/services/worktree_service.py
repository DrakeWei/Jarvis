from __future__ import annotations

import re
import subprocess
from dataclasses import dataclass
from pathlib import Path

from app.core import workspace as workspace_utils

_SLUG_PATTERN = re.compile(r"[^a-z0-9]+")


class WorktreeIsolationError(ValueError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class WorktreeExecutionContext:
    base_workspace_path: Path
    repo_root: Path
    execution_workspace_path: Path
    branch_name: str
    base_revision: str


@dataclass(frozen=True)
class WorktreeCleanupResult:
    execution_workspace_path: Path
    branch_name: str
    base_revision: str
    cleanup_status: str
    preserved_reason: str | None = None


def _slugify(value: str) -> str:
    lowered = value.strip().lower()
    slug = _SLUG_PATTERN.sub("-", lowered).strip("-")
    return slug[:40] or "subagent"


def _run_git(args: list[str], *, cwd: Path) -> str:
    try:
        result = subprocess.run(
            ["git", *args],
            cwd=cwd,
            capture_output=True,
            text=True,
            check=False,
            timeout=20,
        )
    except FileNotFoundError as exc:
        raise WorktreeIsolationError("worktree_create_failed", "Git is not available on this system.") from exc
    except subprocess.TimeoutExpired as exc:
        raise WorktreeIsolationError("worktree_create_failed", f"Timed out running git {' '.join(args)}.") from exc

    output = (result.stdout or result.stderr).strip()
    if result.returncode != 0:
        raise WorktreeIsolationError("worktree_create_failed", output or f"git {' '.join(args)} failed.")
    return output


def _resolve_repo_root(base_workspace: Path) -> Path:
    try:
        resolved = _run_git(["rev-parse", "--show-toplevel"], cwd=base_workspace)
    except WorktreeIsolationError as exc:
        if exc.code == "worktree_create_failed":
            raise WorktreeIsolationError(
                "workspace_not_git_repo",
                f"Workspace is not inside a Git repository: {base_workspace.as_posix()}",
            ) from exc
        raise
    repo_root = Path(resolved.strip()).resolve()
    if not repo_root.exists() or not repo_root.is_dir():
        raise WorktreeIsolationError("workspace_not_git_repo", f"Git repository root not found: {resolved}")
    return repo_root


def prepare_subagent_worktree(base_workspace: Path | str, agent_id: int, name: str) -> WorktreeExecutionContext:
    workspace = workspace_utils.normalize_workspace_path(base_workspace)
    repo_root = _resolve_repo_root(workspace)
    base_revision = _run_git(["rev-parse", "HEAD"], cwd=workspace).strip()
    slug = _slugify(name)
    branch_name = f"jarvis-subagent-{agent_id}-{slug}"[:120]
    worktree_root = repo_root / ".jarvis" / "worktrees"
    execution_workspace = (worktree_root / f"{agent_id}-{slug}").resolve()
    if execution_workspace.exists():
        raise WorktreeIsolationError(
            "worktree_create_failed",
            f"Worktree path already exists: {execution_workspace.as_posix()}",
        )

    worktree_root.mkdir(parents=True, exist_ok=True)
    _run_git(
        ["worktree", "add", "-b", branch_name, execution_workspace.as_posix(), base_revision],
        cwd=repo_root,
    )
    return WorktreeExecutionContext(
        base_workspace_path=workspace,
        repo_root=repo_root,
        execution_workspace_path=execution_workspace,
        branch_name=branch_name,
        base_revision=base_revision,
    )


def finalize_subagent_worktree(
    context: WorktreeExecutionContext,
    *,
    run_failed: bool = False,
) -> WorktreeCleanupResult:
    if run_failed:
        return WorktreeCleanupResult(
            execution_workspace_path=context.execution_workspace_path,
            branch_name=context.branch_name,
            base_revision=context.base_revision,
            cleanup_status="preserved",
            preserved_reason="run_failed",
        )

    try:
        dirty = bool(_run_git(["status", "--porcelain"], cwd=context.execution_workspace_path).strip())
    except WorktreeIsolationError:
        return WorktreeCleanupResult(
            execution_workspace_path=context.execution_workspace_path,
            branch_name=context.branch_name,
            base_revision=context.base_revision,
            cleanup_status="cleanup_failed",
            preserved_reason="cleanup_error",
        )

    if dirty:
        return WorktreeCleanupResult(
            execution_workspace_path=context.execution_workspace_path,
            branch_name=context.branch_name,
            base_revision=context.base_revision,
            cleanup_status="preserved",
            preserved_reason="dirty_worktree",
        )

    try:
        _run_git(["worktree", "remove", "--force", context.execution_workspace_path.as_posix()], cwd=context.repo_root)
    except WorktreeIsolationError:
        return WorktreeCleanupResult(
            execution_workspace_path=context.execution_workspace_path,
            branch_name=context.branch_name,
            base_revision=context.base_revision,
            cleanup_status="cleanup_failed",
            preserved_reason="cleanup_error",
        )

    return WorktreeCleanupResult(
        execution_workspace_path=context.execution_workspace_path,
        branch_name=context.branch_name,
        base_revision=context.base_revision,
        cleanup_status="cleaned",
        preserved_reason=None,
    )
