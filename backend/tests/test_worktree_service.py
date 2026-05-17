from __future__ import annotations

from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase
import subprocess

from app.services import worktree_service


class WorktreeServiceTests(TestCase):
    def _init_git_repo(self, root: Path) -> Path:
        repo = root / "repo"
        repo.mkdir()
        subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
        subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=repo, check=True)
        subprocess.run(["git", "config", "user.name", "Jarvis Test"], cwd=repo, check=True)
        (repo / "README.md").write_text("hello\n")
        subprocess.run(["git", "add", "README.md"], cwd=repo, check=True)
        subprocess.run(["git", "commit", "-q", "-m", "init"], cwd=repo, check=True)
        return repo

    def test_prepare_subagent_worktree_rejects_non_git_workspace(self) -> None:
        with TemporaryDirectory() as temp_dir:
            workspace = Path(temp_dir)
            with self.assertRaises(worktree_service.WorktreeIsolationError) as exc:
                worktree_service.prepare_subagent_worktree(workspace, 12, "Explorer")

        self.assertEqual(exc.exception.code, "workspace_not_git_repo")

    def test_finalize_subagent_worktree_cleans_empty_worktree(self) -> None:
        with TemporaryDirectory() as temp_dir:
            repo = self._init_git_repo(Path(temp_dir))
            context = worktree_service.prepare_subagent_worktree(repo, 7, "Cleanup")

            self.assertTrue(context.execution_workspace_path.exists())
            result = worktree_service.finalize_subagent_worktree(context)
            self.assertFalse(context.execution_workspace_path.exists())

        self.assertEqual(result.cleanup_status, "cleaned")
        self.assertIsNone(result.preserved_reason)

    def test_finalize_subagent_worktree_preserves_dirty_worktree(self) -> None:
        with TemporaryDirectory() as temp_dir:
            repo = self._init_git_repo(Path(temp_dir))
            context = worktree_service.prepare_subagent_worktree(repo, 8, "Dirty")
            (context.execution_workspace_path / "notes.txt").write_text("dirty\n")

            result = worktree_service.finalize_subagent_worktree(context)
            self.assertTrue(context.execution_workspace_path.exists())

        self.assertEqual(result.cleanup_status, "preserved")
        self.assertEqual(result.preserved_reason, "dirty_worktree")
