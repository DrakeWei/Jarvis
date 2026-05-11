from __future__ import annotations

import json
import subprocess
from pathlib import Path

from app.core.config import settings


class ToolBroker:
    def __init__(self) -> None:
        self.project_root = settings.project_root

    def run(self, tool_name: str, payload: dict[str, object]) -> tuple[str, str]:
        if tool_name == "list_files":
            return "completed", self._list_files()
        if tool_name == "read_file":
            path = str(payload.get("path", ""))
            return "completed", self._read_file(path)
        if tool_name == "write_file":
            path = str(payload.get("path", ""))
            content = str(payload.get("content", ""))
            return self._write_file(path, content)
        if tool_name == "edit_file":
            path = str(payload.get("path", ""))
            old_text = str(payload.get("old_text", ""))
            new_text = str(payload.get("new_text", ""))
            return self._edit_file(path, old_text, new_text)
        if tool_name == "bash":
            command = str(payload.get("command", ""))
            return self._run_bash(command)
        return "error", f"Unknown tool '{tool_name}'"

    def serialize_input(self, payload: dict[str, object]) -> str:
        return json.dumps(payload, ensure_ascii=True)

    def _safe_path(self, value: str) -> Path:
        path = (self.project_root / value).resolve()
        if not path.is_relative_to(self.project_root):
            raise ValueError("Path escapes project root")
        return path

    def _list_files(self) -> str:
        result = subprocess.run(
            ["rg", "--files", str(self.project_root)],
            capture_output=True,
            text=True,
            check=False,
        )
        output = result.stdout.strip() or result.stderr.strip() or "(no files)"
        lines = output.splitlines()[:80]
        return "\n".join(lines)

    def _read_file(self, path: str) -> str:
        target = self._safe_path(path)
        if not target.exists():
            return f"File not found: {path}"
        return "\n".join(target.read_text().splitlines()[:120])

    def _write_file(self, path: str, content: str) -> tuple[str, str]:
        target = self._safe_path(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content)
        return "completed", f"Wrote {len(content)} bytes to {path}"

    def _edit_file(self, path: str, old_text: str, new_text: str) -> tuple[str, str]:
        if not old_text:
            return "error", "edit_file requires a non-empty old_text."
        target = self._safe_path(path)
        if not target.exists():
            return "error", f"File not found: {path}"
        current = target.read_text()
        if old_text not in current:
            return "error", f"Target text not found in {path}"
        target.write_text(current.replace(old_text, new_text, 1))
        return "completed", f"Edited {path}"

    def _run_bash(self, command: str) -> tuple[str, str]:
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


broker = ToolBroker()
