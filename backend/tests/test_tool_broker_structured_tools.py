from __future__ import annotations

from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase
from unittest.mock import patch
import subprocess
import sys

from app.mcp.registry import local_tool_definitions
from app.tools.broker import ToolBroker


class ToolBrokerStructuredToolTests(TestCase):
    def test_local_tool_definitions_include_structured_read_only_coding_tools(self) -> None:
        names = {tool.name for tool in local_tool_definitions()}
        self.assertTrue(
            {"search_text", "read_file_range", "show_status", "show_diff", "run_test", "apply_patch", "web_search"}.issubset(names)
        )

    def _init_git_repo(self, root: Path) -> Path:
        repo = root / "repo"
        repo.mkdir()
        subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
        subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=repo, check=True)
        subprocess.run(["git", "config", "user.name", "Jarvis Test"], cwd=repo, check=True)
        return repo

    def test_read_file_range_returns_numbered_lines(self) -> None:
        with TemporaryDirectory() as tmpdir:
            repo = self._init_git_repo(Path(tmpdir))
            target = repo / "sample.txt"
            target.write_text("one\ntwo\nthree\nfour\n")
            broker = ToolBroker(repo)

            status, output = broker.run(
                "read_file_range",
                {"path": "sample.txt", "start_line": 2, "end_line": 3},
            )

        self.assertEqual(status, "completed")
        self.assertEqual(output, "2: two\n3: three")

    def test_search_text_returns_matching_lines(self) -> None:
        with TemporaryDirectory() as tmpdir:
            repo = self._init_git_repo(Path(tmpdir))
            target = repo / "sample.txt"
            target.write_text("alpha\nbeta target\ncharlie target\n")
            broker = ToolBroker(repo)

            status, output = broker.run("search_text", {"query": "target", "max_results": 5})

        self.assertEqual(status, "completed")
        self.assertIn("sample.txt:2:", output)
        self.assertIn("sample.txt:3:", output)

    def test_show_status_reports_clean_and_dirty_states(self) -> None:
        with TemporaryDirectory() as tmpdir:
            repo = self._init_git_repo(Path(tmpdir))
            target = repo / "tracked.txt"
            target.write_text("hello\n")
            subprocess.run(["git", "add", "tracked.txt"], cwd=repo, check=True)
            subprocess.run(["git", "commit", "-q", "-m", "init"], cwd=repo, check=True)
            broker = ToolBroker(repo)

            clean_status, clean_output = broker.run("show_status", {})
            target.write_text("dirty\n")
            dirty_status, dirty_output = broker.run("show_status", {})

        self.assertEqual(clean_status, "completed")
        self.assertEqual(clean_output, "Working tree is clean.")
        self.assertEqual(dirty_status, "completed")
        self.assertIn("tracked.txt", dirty_output)

    def test_show_diff_returns_diff_text(self) -> None:
        with TemporaryDirectory() as tmpdir:
            repo = self._init_git_repo(Path(tmpdir))
            target = repo / "tracked.txt"
            target.write_text("hello\n")
            subprocess.run(["git", "add", "tracked.txt"], cwd=repo, check=True)
            subprocess.run(["git", "commit", "-q", "-m", "init"], cwd=repo, check=True)
            target.write_text("hello world\n")
            broker = ToolBroker(repo)

            status, output = broker.run("show_diff", {"path": "tracked.txt"})

        self.assertEqual(status, "completed")
        self.assertIn("diff --git", output)
        self.assertIn("+hello world", output)

    def test_run_test_executes_argv_without_shell(self) -> None:
        with TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            broker = ToolBroker(root)

            result = broker.run("run_test", {"argv": ["python3", "-c", "print('ok')"]})

        self.assertEqual(result.status, "completed")
        self.assertIn("exit_code=0", result.output)
        self.assertIn("ok", result.output)
        self.assertEqual(result.payload["classification"], "verification")

    def test_run_test_blocks_package_install_commands(self) -> None:
        with TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            broker = ToolBroker(root)

            result = broker.run("run_test", {"argv": ["python3", "-m", "pip", "install", "pypdf"]})

        self.assertEqual(result.status, "blocked")
        self.assertEqual(result.payload["classification"], "blocked_mutation")
        self.assertIn("Use bash with approval", result.output)

    def test_run_test_prefers_workspace_venv_python(self) -> None:
        with TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            venv_bin = root / ".venv" / "bin"
            venv_bin.mkdir(parents=True)
            (venv_bin / "python").symlink_to(Path(sys.executable))
            broker = ToolBroker(root)

            result = broker.run("run_test", {"argv": ["python3", "-c", "import sys; print(sys.executable)"]})

        self.assertEqual(result.status, "completed")
        self.assertEqual(result.payload["target_python_executable"], str((venv_bin / "python").resolve()))
        self.assertTrue(result.payload["used_workspace_venv"])

    def test_run_test_normalizes_python_to_python3_when_python_is_missing(self) -> None:
        with TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            broker = ToolBroker(root)

            with patch("app.tools.broker.shutil.which") as which_mock:
                which_mock.side_effect = lambda name: None if name == "python" else ("/usr/bin/python3" if name == "python3" else None)
                normalized = broker._normalize_test_command(["python", "-c", "print('ok')"])

        self.assertEqual(normalized[0], "python3")

    def test_bash_prefers_workspace_venv_python_for_simple_python_commands(self) -> None:
        with TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            venv_bin = root / ".venv" / "bin"
            venv_bin.mkdir(parents=True)
            (venv_bin / "python").symlink_to(Path(sys.executable))
            broker = ToolBroker(root)

            with patch("app.tools.broker.subprocess.run") as run_mock:
                run_mock.return_value = subprocess.CompletedProcess(
                    args=[(venv_bin / "python").as_posix(), "-c", "print('ok')"],
                    returncode=0,
                    stdout="ok\n",
                    stderr="",
                )
                status, output = broker.run("bash", {"command": "python3 -c \"print('ok')\""})

        self.assertEqual(status, "completed")
        called_command = run_mock.call_args.args[0]
        self.assertEqual(Path(called_command[0]).resolve(), (venv_bin / "python").resolve())
        self.assertIn("exit_code=0", output)

    def test_bash_extends_timeout_for_package_installs(self) -> None:
        with TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            broker = ToolBroker(root)

            with patch("app.tools.broker.subprocess.run") as run_mock:
                run_mock.return_value = subprocess.CompletedProcess(
                    args=["python3", "-m", "pip", "install", "Pillow"],
                    returncode=0,
                    stdout="installed\n",
                    stderr="",
                )
                status, output = broker.run("bash", {"command": "python3 -m pip install Pillow"})

        self.assertEqual(status, "completed")
        self.assertEqual(run_mock.call_args.kwargs["timeout"], 300)
        self.assertIn("exit_code=0", output)

    def test_apply_patch_updates_file_inside_workspace(self) -> None:
        with TemporaryDirectory() as tmpdir:
            repo = self._init_git_repo(Path(tmpdir))
            target = repo / "tracked.txt"
            target.write_text("hello\n")
            subprocess.run(["git", "add", "tracked.txt"], cwd=repo, check=True)
            subprocess.run(["git", "commit", "-q", "-m", "init"], cwd=repo, check=True)
            broker = ToolBroker(repo)
            patch = """diff --git a/tracked.txt b/tracked.txt
index ce01362..3b18e51 100644
--- a/tracked.txt
+++ b/tracked.txt
@@ -1 +1 @@
-hello
+hello world
"""

            status, output = broker.run("apply_patch", {"patch": patch})
            updated_text = target.read_text()

        self.assertEqual(status, "completed")
        self.assertIn("Applied patch touching 1 file", output)
        self.assertEqual(updated_text, "hello world\n")

    def test_apply_patch_updates_file_inside_workspace_with_structured_patch_format(self) -> None:
        with TemporaryDirectory() as tmpdir:
            repo = self._init_git_repo(Path(tmpdir))
            target = repo / "tracked.txt"
            target.write_text("hello\n")
            subprocess.run(["git", "add", "tracked.txt"], cwd=repo, check=True)
            subprocess.run(["git", "commit", "-q", "-m", "init"], cwd=repo, check=True)
            broker = ToolBroker(repo)
            patch = """*** Begin Patch
*** Update File: tracked.txt
@@
-hello
+hello world
*** End Patch"""

            status, output = broker.run("apply_patch", {"patch": patch})
            updated_text = target.read_text()

        self.assertEqual(status, "completed")
        self.assertIn("Applied patch touching 1 file", output)
        self.assertEqual(updated_text, "hello world\n")

    def test_apply_patch_blocks_paths_outside_workspace(self) -> None:
        with TemporaryDirectory() as tmpdir:
            repo = self._init_git_repo(Path(tmpdir))
            (repo / "inner").mkdir()
            target = repo / "inner" / "tracked.txt"
            target.write_text("hello\n")
            subprocess.run(["git", "add", "inner/tracked.txt"], cwd=repo, check=True)
            subprocess.run(["git", "commit", "-q", "-m", "init"], cwd=repo, check=True)
            broker = ToolBroker(repo / "inner")
            patch = """diff --git a/other.txt b/other.txt
index ce01362..3b18e51 100644
--- a/other.txt
+++ b/other.txt
@@ -1 +1 @@
-hello
+hello world
"""

            status, output = broker.run("apply_patch", {"patch": patch})

        self.assertEqual(status, "blocked")
        self.assertIn("outside the current session workspace", output)
