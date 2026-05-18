from __future__ import annotations

from contextlib import contextmanager
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import IsolatedAsyncioTestCase, TestCase
from unittest.mock import AsyncMock, patch
import subprocess

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

import app.services.approval_service as approval_service
import app.services.checkpoint_service as checkpoint_service
import app.services.git_service as git_service
import app.services.memory_service as memory_service
import app.services.session_service as session_service
import app.services.turn_service as turn_service
from app.db.base import Base
from app.models import ApprovalRecord, MessageRecord, SessionRecord, TurnRecord
from app.schemas.events import MessageCreate, SessionCreate
from app.runtime.manager import RuntimeManager


class GitServiceTests(IsolatedAsyncioTestCase):
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

    async def test_switch_session_branch_rotates_branch_context(self) -> None:
        with TemporaryDirectory() as temp_dir:
            repo, branch = self._init_git_repo(Path(temp_dir))
            subprocess.run(["git", "branch", "feature/test"], cwd=repo, check=True)
            with self._create_session() as db:
                db.add(
                    SessionRecord(
                        id="session-1",
                        title="Repo session",
                        workspace_mode="bound",
                        canonical_workspace_path=repo.as_posix(),
                        workspace_fingerprint="workspace-fp",
                        workspace_label="repo",
                        repo_root=repo.resolve().as_posix(),
                        git_enabled=True,
                        lead_branch=branch,
                        head_revision="head-a",
                        working_tree_status="clean",
                        detached_head=False,
                        branch_context_id="branch-a",
                        status="idle",
                    )
                )
                db.commit()
            runtime = RuntimeManager()
            with patch.object(session_service, "create_session", self._create_session), patch.object(
                turn_service,
                "create_session",
                self._create_session,
            ), patch.object(
                approval_service,
                "create_session",
                self._create_session,
            ), patch.object(
                memory_service,
                "create_session",
                self._create_session,
            ), patch.object(
                checkpoint_service,
                "latest_resumable_checkpoint_context",
                return_value=None,
            ), patch.object(
                runtime,
                "publish",
                new=AsyncMock(),
            ):
                result = await runtime.switch_session_branch("session-1", "feature/test")

            with self._create_session() as db:
                row = db.get(SessionRecord, "session-1")

        self.assertEqual(result.target_branch, "feature/test")
        self.assertNotEqual(row.branch_context_id if row else None, "branch-a")
        self.assertEqual(row.lead_branch if row else None, "feature/test")

    async def test_switch_session_branch_rejects_dirty_worktree(self) -> None:
        with TemporaryDirectory() as temp_dir:
            repo, branch = self._init_git_repo(Path(temp_dir))
            (repo / "README.md").write_text("dirty\n")
            with self._create_session() as db:
                db.add(
                    SessionRecord(
                        id="session-1",
                        title="Repo session",
                        workspace_mode="bound",
                        canonical_workspace_path=repo.as_posix(),
                        workspace_fingerprint="workspace-fp",
                        workspace_label="repo",
                        repo_root=repo.resolve().as_posix(),
                        git_enabled=True,
                        lead_branch=branch,
                        head_revision="head-a",
                        working_tree_status="dirty",
                        detached_head=False,
                        branch_context_id="branch-a",
                        status="idle",
                    )
                )
                db.commit()
            runtime = RuntimeManager()
            with patch.object(session_service, "create_session", self._create_session), patch.object(
                turn_service,
                "create_session",
                self._create_session,
            ), patch.object(
                approval_service,
                "create_session",
                self._create_session,
            ), patch.object(
                memory_service,
                "create_session",
                self._create_session,
            ):
                with self.assertRaises(ValueError):
                    await runtime.switch_session_branch("session-1", "feature/test")

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
            blocking = git_service.has_blocking_branch_switch_changes(repo)

        self.assertTrue(clean_state.git_enabled)
        self.assertEqual(clean_state.lead_branch, branch)
        self.assertEqual(clean_state.working_tree_status, "clean")
        self.assertEqual(dirty_state.working_tree_status, "dirty")
        self.assertTrue(blocking)

    def test_untracked_files_do_not_block_branch_switch_precheck(self) -> None:
        with TemporaryDirectory() as temp_dir:
            repo, _branch = self._init_git_repo(Path(temp_dir))
            (repo / ".idea").mkdir()
            (repo / ".idea" / "workspace.xml").write_text("x\n")
            state = git_service.inspect_workspace_git_state(repo)
            blocking = git_service.has_blocking_branch_switch_changes(repo)

        self.assertEqual(state.working_tree_status, "dirty")
        self.assertFalse(blocking)

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

    def test_list_and_switch_local_branches(self) -> None:
        with TemporaryDirectory() as temp_dir:
            repo, branch = self._init_git_repo(Path(temp_dir))
            subprocess.run(["git", "branch", "feature/test"], cwd=repo, check=True)

            listed = git_service.list_local_branches(repo)
            switched = git_service.switch_branch(repo, "feature/test")
            created = git_service.create_and_switch_branch(repo, "feature/new-ui")

        self.assertIn(branch, listed.branches)
        self.assertIn("feature/test", listed.branches)
        self.assertEqual(switched.lead_branch, "feature/test")
        self.assertEqual(created.lead_branch, "feature/new-ui")


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

    def test_branch_context_filters_messages_turns_and_approvals(self) -> None:
        with self._create_session() as db:
            db.add(
                SessionRecord(
                    id="session-1",
                    title="Repo session",
                    workspace_mode="bound",
                    canonical_workspace_path="/tmp/workspace",
                    workspace_fingerprint="workspace-fp",
                    workspace_label="repo",
                    branch_context_id="branch-a",
                    git_enabled=False,
                    detached_head=False,
                    status="idle",
                )
            )
            db.add(MessageRecord(session_id="session-1", branch_context_id="branch-a", role="user", content="main work"))
            db.add(MessageRecord(session_id="session-1", branch_context_id="branch-b", role="user", content="other branch work"))
            db.add(TurnRecord(session_id="session-1", branch_context_id="branch-a", status="running"))
            db.add(TurnRecord(session_id="session-1", branch_context_id="branch-b", status="running"))
            db.add(ApprovalRecord(session_id="session-1", branch_context_id="branch-a", approval_type="bash", status="pending", prompt="a"))
            db.add(ApprovalRecord(session_id="session-1", branch_context_id="branch-b", approval_type="bash", status="pending", prompt="b"))
            db.commit()

        with patch.object(session_service, "create_session", self._create_session), patch.object(
            turn_service,
            "create_session",
            self._create_session,
        ), patch.object(
            approval_service,
            "create_session",
            self._create_session,
        ):
            messages = session_service.list_message_records("session-1")
            turns = turn_service.list_turns("session-1", branch_context_id="branch-a")
            approvals = approval_service.list_approvals("session-1", branch_context_id="branch-a")

        self.assertEqual(len(messages), 1)
        self.assertEqual(messages[0]["content"], "main work")
        self.assertEqual(len(turns), 1)
        self.assertEqual(turns[0].branch_context_id, "branch-a")
        self.assertEqual(len(approvals), 1)
        self.assertEqual(approvals[0].prompt, "a")


class RuntimeBranchSwitchTests(IsolatedAsyncioTestCase):
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
