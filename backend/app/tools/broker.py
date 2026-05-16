from __future__ import annotations

import json
import subprocess
from pathlib import Path

from app.core.config import settings
from app.core import workspace as workspace_utils


class ToolBroker:
    def __init__(
        self,
        project_root: Path | str | None = None,
        *,
        allowed_external_reads: list[Path] | None = None,
        write_enabled: bool = True,
    ) -> None:
        self.project_root = Path(project_root).resolve() if project_root else settings.project_root
        self.allowed_external_reads = [path.resolve() for path in (allowed_external_reads or [])]
        self.write_enabled = write_enabled
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
                return "completed", self._list_files(str(payload.get("path", "")))
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

    def _is_allowed_external_read(self, path: Path) -> bool:
        for allowed in self.allowed_external_reads:
            if allowed.is_dir() and workspace_utils.path_within(allowed, path):
                return True
            if path == allowed:
                return True
        return False

    def _resolve_path(self, value: str, *, mode: str) -> Path:
        if value.strip():
            raw = Path(value).expanduser()
            path = raw.resolve() if raw.is_absolute() else (self.project_root / raw).resolve()
        else:
            path = self.project_root
        if workspace_utils.path_within(self.project_root, path):
            if mode == "write" and not self.write_enabled:
                raise ValueError(
                    "This session is in Default Conversations mode. Bind it to a workspace before writing files."
                )
            return path
        if mode in {"read", "list"} and self._is_allowed_external_read(path):
            return path
        if mode == "write":
            raise ValueError(
                "Write target is outside the current session workspace. Open a session bound to that workspace or rebind this session first."
            )
        raise ValueError("Path is outside the current session workspace and is not approved for explicit read access.")

    def _safe_path(self, value: str, *, mode: str) -> Path:
        if mode in {"read", "write"} and not value.strip():
            raise ValueError("Path is required.")
        path = self._resolve_path(value, mode=mode)
        return path

    def _list_files(self, path: str = "") -> str:
        target_root = self._resolve_path(path, mode="list")
        if not target_root.exists():
            return "(no files)"
        if not target_root.is_dir():
            return f"Path is a file, not a directory: {target_root.as_posix()}"
        try:
            result = subprocess.run(
                ["rg", "--files", str(target_root)],
                capture_output=True,
                text=True,
                check=False,
            )
            output = result.stdout.strip() or result.stderr.strip() or "(no files)"
            lines = output.splitlines()[:80]
            return "\n".join(lines)
        except FileNotFoundError:
            files = [
                candidate.relative_to(target_root).as_posix()
                for candidate in sorted(self._iter_visible_files(target_root))
            ]
            if not files:
                return "(no files)"
            return "\n".join(files[:80])

    def _read_file(self, path: str) -> tuple[str, str]:
        target = self._safe_path(path, mode="read")
        if not target.exists():
            return "error", f"File not found: {path}"
        if target.is_dir():
            return "error", f"Path is a directory, not a file: {path}"
        return "completed", "\n".join(target.read_text().splitlines()[:120])

    def _write_file(self, path: str, content: str) -> tuple[str, str]:
        target = self._safe_path(path, mode="write")
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content)
        return "completed", f"Wrote {len(content)} bytes to {path}"

    def _edit_file(self, path: str, old_text: str, new_text: str) -> tuple[str, str]:
        if not old_text:
            return "error", "edit_file requires a non-empty old_text."
        if old_text == new_text:
            return "error", "edit_file old_text and new_text must differ."
        target = self._safe_path(path, mode="write")
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

    def _iter_visible_files(self, root: Path):
        import os

        for current_root, dirs, files in os.walk(root):
            dirs[:] = [
                directory
                for directory in dirs
                if directory not in self._ignored_dir_names and not directory.startswith(".")
            ]
            for filename in files:
                if filename.startswith("."):
                    continue
                yield Path(current_root) / filename


broker = ToolBroker()
