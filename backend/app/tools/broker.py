from __future__ import annotations

import json
import subprocess
from pathlib import Path

from app.core.config import settings


class ToolBroker:
    def __init__(self, project_root: Path | str | None = None) -> None:
        self.project_root = Path(project_root).resolve() if project_root else settings.project_root
        self._ignored_dir_names = {
            ".git",
            ".hg",
            ".svn",
            ".venv",
            "node_modules",
            "__pycache__",
            ".mypy_cache",
            ".pytest_cache",
            ".ruff_cache",
            ".tox",
            "target",
            "dist",
            "build",
        }

    def run(self, tool_name: str, payload: dict[str, object]) -> tuple[str, str]:
        try:
            if tool_name == "list_files":
                return "completed", self._list_files()
            if tool_name == "read_file":
                return self._read_file(str(payload.get("path", "")))
            if tool_name == "write_file":
                return self._write_file(str(payload.get("path", "")), str(payload.get("content", "")))
            if tool_name == "edit_file":
                return self._edit_file(
                    str(payload.get("path", "")),
                    str(payload.get("old_text", "")),
                    str(payload.get("new_text", "")),
                )
            if tool_name == "bash":
                return self._run_bash(str(payload.get("command", "")))
            return "error", f"Unknown tool '{tool_name}'"
        except ValueError as exc:
            return "blocked", str(exc)

    def serialize_input(self, payload: dict[str, object]) -> str:
        return json.dumps(payload, ensure_ascii=True)

    def _safe_path(self, value: str) -> Path:
        if not value.strip():
            raise ValueError("Path is required.")
        path = (self.project_root / value).resolve()
        if not path.is_relative_to(self.project_root):
            raise ValueError("Path escapes project root")
        return path

    def _list_files(self) -> str:
        try:
            result = subprocess.run(
                ["rg", "--files", str(self.project_root)],
                capture_output=True,
                text=True,
                check=False,
            )
            output = result.stdout.strip() or result.stderr.strip() or "(no files)"
            lines = output.splitlines()[:80]
            return "\n".join(lines)
        except FileNotFoundError:
            files = [
                path.relative_to(self.project_root).as_posix()
                for path in sorted(self._iter_visible_files())
            ]
            if not files:
                return "(no files)"
            return "\n".join(files[:80])

    def _read_file(self, path: str) -> tuple[str, str]:
        target = self._safe_path(path)
        if not target.exists():
            return "error", f"File not found: {path}"
        return "completed", "\n".join(target.read_text().splitlines()[:120])

    def _write_file(self, path: str, content: str) -> tuple[str, str]:
        target = self._safe_path(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content)
        return "completed", f"Wrote {len(content)} bytes to {path}"

    def _edit_file(self, path: str, old_text: str, new_text: str) -> tuple[str, str]:
        if not old_text:
            return "error", "edit_file requires a non-empty old_text."
        if old_text == new_text:
            return "error", "edit_file old_text and new_text must differ."
        target = self._safe_path(path)
        if not target.exists():
            return "error", f"File not found: {path}"
        current = target.read_text()
        if old_text not in current:
            return "error", f"Target text not found in {path}"
        target.write_text(current.replace(old_text, new_text, 1))
        return "completed", f"Edited {path}"

    def _run_bash(self, command: str) -> tuple[str, str]:
        if not command.strip():
            return "error", "bash requires a command after 'bash:'."
        banned = ["rm -rf", "sudo", "shutdown", "reboot"]
        if any(token in command for token in banned):
            return "blocked", "Blocked potentially destructive command."
        result = subprocess.run(
            command,
            shell=True,
            cwd=self.project_root,
            capture_output=True,
            text=True,
            timeout=20,
        )
        output = (result.stdout + result.stderr).strip()[:10000] or "(no output)"
        return ("completed" if result.returncode == 0 else "error"), output

    def _iter_visible_files(self):
        import os

        for root, dirs, files in os.walk(self.project_root):
            dirs[:] = [
                directory
                for directory in dirs
                if directory not in self._ignored_dir_names and not directory.startswith(".")
            ]
            for filename in files:
                if filename.startswith("."):
                    continue
                yield Path(root) / filename


broker = ToolBroker()
