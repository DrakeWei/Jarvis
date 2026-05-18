from __future__ import annotations

import subprocess
from dataclasses import dataclass
from pathlib import Path

from app.core import workspace as workspace_utils


@dataclass(frozen=True)
class GitWorkspaceState:
    repo_root: str | None
    git_enabled: bool
    lead_branch: str | None
    head_revision: str | None
    working_tree_status: str | None
    detached_head: bool


@dataclass(frozen=True)
class GitBranchListState:
    current_branch: str | None
    branches: list[str]


def _run_git(args: list[str], *, cwd: Path) -> tuple[bool, str]:
    try:
        result = subprocess.run(
            ["git", *args],
            cwd=cwd,
            capture_output=True,
            text=True,
            check=False,
            timeout=20,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return False, ""
    output = (result.stdout or result.stderr).strip()
    return result.returncode == 0, output


def inspect_workspace_git_state(workspace: Path | str | None) -> GitWorkspaceState:
    if workspace is None:
        return GitWorkspaceState(None, False, None, None, None, False)

    try:
        base = workspace_utils.normalize_workspace_path(workspace)
    except ValueError:
        return GitWorkspaceState(None, False, None, None, None, False)

    ok, repo_root_text = _run_git(["rev-parse", "--show-toplevel"], cwd=base)
    if not ok or not repo_root_text:
        return GitWorkspaceState(None, False, None, None, None, False)

    repo_root = Path(repo_root_text).resolve().as_posix()
    detached_head = False

    ok, branch_name = _run_git(["symbolic-ref", "--short", "HEAD"], cwd=base)
    if not ok:
        detached_head = True
        ok, branch_name = _run_git(["branch", "--show-current"], cwd=base)
    branch_name = branch_name or None

    ok, head_revision = _run_git(["rev-parse", "HEAD"], cwd=base)
    head_revision = head_revision or None

    ok, status_output = _run_git(["status", "--porcelain"], cwd=base)
    if detached_head:
        working_tree_status = "detached"
    elif ok:
        working_tree_status = "dirty" if status_output else "clean"
    else:
        working_tree_status = "unknown"

    return GitWorkspaceState(
        repo_root=repo_root,
        git_enabled=True,
        lead_branch=branch_name,
        head_revision=head_revision,
        working_tree_status=working_tree_status,
        detached_head=detached_head,
    )


def has_blocking_branch_switch_changes(workspace: Path | str | None) -> bool:
    base = _require_git_workspace(workspace)
    ok, output = _run_git(["status", "--porcelain", "--untracked-files=no"], cwd=base)
    if not ok:
        raise ValueError(output or "Failed to inspect Git working tree state.")
    return bool(output.strip())


def _require_git_workspace(workspace: Path | str | None) -> Path:
    state = inspect_workspace_git_state(workspace)
    if not state.git_enabled or not state.repo_root:
        raise ValueError("The workspace is not inside a Git repository.")
    return Path(state.repo_root)


def list_local_branches(workspace: Path | str | None) -> GitBranchListState:
    base = _require_git_workspace(workspace)
    ok, output = _run_git(["for-each-ref", "refs/heads", "--format=%(refname:short)"], cwd=base)
    if not ok:
        raise ValueError(output or "Failed to list Git branches.")
    branches = sorted({line.strip() for line in output.splitlines() if line.strip()})
    state = inspect_workspace_git_state(base)
    return GitBranchListState(current_branch=state.lead_branch, branches=branches)


def validate_new_branch_name(name: str) -> str:
    candidate = name.strip()
    if not candidate:
        raise ValueError("Branch name is required.")
    ok, output = _run_git(["check-ref-format", "--branch", candidate], cwd=Path.cwd())
    if not ok:
        raise ValueError(output or f"Invalid Git branch name: {candidate}")
    return candidate


def switch_branch(workspace: Path | str | None, branch_name: str) -> GitWorkspaceState:
    base = _require_git_workspace(workspace)
    target = branch_name.strip()
    if not target:
        raise ValueError("Target branch is required.")
    ok, output = _run_git(["switch", target], cwd=base)
    if not ok:
        raise ValueError(output or f"Failed to switch to branch '{target}'.")
    return inspect_workspace_git_state(base)


def create_and_switch_branch(workspace: Path | str | None, branch_name: str) -> GitWorkspaceState:
    base = _require_git_workspace(workspace)
    target = validate_new_branch_name(branch_name)
    ok, output = _run_git(["switch", "-c", target], cwd=base)
    if not ok:
        raise ValueError(output or f"Failed to create and switch to branch '{target}'.")
    return inspect_workspace_git_state(base)
