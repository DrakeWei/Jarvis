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
