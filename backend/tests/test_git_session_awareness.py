from __future__ import annotations

from contextlib import contextmanager
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase
from unittest.mock import patch
import subprocess

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

import app.services.git_service as git_service
import app.services.session_service as session_service
from app.db.base import Base
from app.models import SessionRecord
from app.schemas.events import SessionCreate


class GitServiceTests(TestCase):
    def _init_git_repo(self, root: Path) -> tuple[Path, str]:
        repo = root / "repo"
        repo.mkdir()
        subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
        subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=repo, check=True)
        subprocess.run(["git", "config", "user.name", "Jarvis Test"], cwd=repo, check=True)
        (repo / "README.md").write_text("hello\n")
        subprocess.run(["git", "add", "README.md"], cwd=repo, check=True)
        subprocess.run(["git", "commit", "-q", "-m", "init"], cwd=repo, check=True)
        branch = subprocess.run(
            ["git", "branch", "--show-current"],
            cwd=repo,
            capture_output=True,
            text=True,
            check=True,
        ).stdout.strip()
        return repo, branch

    def test_inspect_workspace_git_state_returns_non_git_defaults(self) -> None:
        with TemporaryDirectory() as temp_dir:
            state = git_service.inspect_workspace_git_state(Path(temp_dir))

        self.assertFalse(state.git_enabled)
        self.assertIsNone(state.repo_root)
        self.assertIsNone(state.lead_branch)

    def test_inspect_workspace_git_state_detects_clean_and_dirty_repo(self) -> None:
        with TemporaryDirectory() as temp_dir:
            repo, branch = self._init_git_repo(Path(temp_dir))
            clean_state = git_service.inspect_workspace_git_state(repo)
            (repo / "README.md").write_text("hello world\n")
            dirty_state = git_service.inspect_workspace_git_state(repo)

        self.assertTrue(clean_state.git_enabled)
        self.assertEqual(clean_state.lead_branch, branch)
        self.assertEqual(clean_state.working_tree_status, "clean")
        self.assertEqual(dirty_state.working_tree_status, "dirty")

    def test_inspect_workspace_git_state_detects_detached_head(self) -> None:
        with TemporaryDirectory() as temp_dir:
            repo, _branch = self._init_git_repo(Path(temp_dir))
            revision = subprocess.run(
                ["git", "rev-parse", "HEAD"],
                cwd=repo,
                capture_output=True,
                text=True,
                check=True,
            ).stdout.strip()
            subprocess.run(["git", "checkout", "--detach", revision], cwd=repo, check=True, capture_output=True, text=True)
            state = git_service.inspect_workspace_git_state(repo)

        self.assertTrue(state.git_enabled)
        self.assertTrue(state.detached_head)
        self.assertEqual(state.working_tree_status, "detached")


class SessionServiceGitAwarenessTests(TestCase):
    def setUp(self) -> None:
        super().setUp()
        self.engine = create_engine("sqlite:///:memory:", future=True)
        Base.metadata.create_all(bind=self.engine)
        self.SessionLocal = sessionmaker(bind=self.engine, autoflush=False, autocommit=False, future=True)

    @contextmanager
    def _create_session(self):
        db = self.SessionLocal()
        try:
            yield db
        finally:
            db.close()

    def _init_git_repo(self, root: Path) -> tuple[Path, str]:
        repo = root / "repo"
        repo.mkdir()
        subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
        subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=repo, check=True)
        subprocess.run(["git", "config", "user.name", "Jarvis Test"], cwd=repo, check=True)
        (repo / "README.md").write_text("hello\n")
        subprocess.run(["git", "add", "README.md"], cwd=repo, check=True)
        subprocess.run(["git", "commit", "-q", "-m", "init"], cwd=repo, check=True)
        branch = subprocess.run(
            ["git", "branch", "--show-current"],
            cwd=repo,
            capture_output=True,
            text=True,
            check=True,
        ).stdout.strip()
        return repo, branch

    def test_create_session_record_persists_git_metadata(self) -> None:
        with TemporaryDirectory() as temp_dir:
            repo, branch = self._init_git_repo(Path(temp_dir))
            with patch.object(session_service, "create_session", self._create_session):
                summary = session_service.create_session_record(
                    SessionCreate(title="Repo session", workspace_mode="bound", workspace_path=repo.as_posix())
                )

        self.assertTrue(summary.git_enabled)
        self.assertEqual(summary.lead_branch, branch)
        self.assertEqual(summary.working_tree_status, "clean")
        self.assertEqual(summary.repo_root, repo.resolve().as_posix())

    def test_list_sessions_refreshes_dirty_git_state(self) -> None:
        with TemporaryDirectory() as temp_dir:
            repo, branch = self._init_git_repo(Path(temp_dir))
            with self._create_session() as db:
                db.add(
                    SessionRecord(
                        id="session-1",
                        title="Repo session",
                        workspace_mode="bound",
                        canonical_workspace_path=repo.as_posix(),
                        workspace_fingerprint="workspace-fp",
                        workspace_label="repo",
                        git_enabled=False,
                        detached_head=False,
                        status="idle",
                    )
                )
                db.commit()
            (repo / "README.md").write_text("dirty\n")
            with patch.object(session_service, "create_session", self._create_session):
                summaries = session_service.list_sessions()

        self.assertEqual(len(summaries), 1)
        self.assertTrue(summaries[0].git_enabled)
        self.assertEqual(summaries[0].lead_branch, branch)
        self.assertEqual(summaries[0].working_tree_status, "dirty")
