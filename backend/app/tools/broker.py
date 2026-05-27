from __future__ import annotations

import json
import re
import shlex
import subprocess
from pathlib import Path
import shutil
import tempfile

from app.core.config import settings
from app.core import workspace as workspace_utils
from app.mcp.registry import ToolExecutionResult


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

    def run(self, tool_name: str, payload: dict[str, object]) -> tuple[str, str] | ToolExecutionResult:
        try:
            if tool_name == "list_files":
                return "completed", self._list_files(str(payload.get("path", "")))
            if tool_name == "read_file":
                return self._read_file(str(payload.get("path", "")))
            if tool_name == "read_file_range":
                return self._read_file_range(
                    str(payload.get("path", "")),
                    payload.get("start_line"),
                    payload.get("end_line"),
                )
            if tool_name == "search_text":
                return self._search_text(
                    str(payload.get("query", "")),
                    str(payload.get("path", "")),
                    payload.get("max_results"),
                )
            if tool_name == "show_status":
                return self._show_status()
            if tool_name == "show_diff":
                return self._show_diff(str(payload.get("path", "")))
            if tool_name == "run_test":
                return self._run_test(payload.get("argv"))
            if tool_name == "apply_patch":
                return self._apply_patch(str(payload.get("patch", "")))
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

    def _read_file_range(self, path: str, start_line: object, end_line: object) -> tuple[str, str]:
        target = self._safe_path(path, mode="read")
        if not target.exists():
            return "error", f"File not found: {path}"
        if target.is_dir():
            return "error", f"Path is a directory, not a file: {path}"
        start = start_line if isinstance(start_line, int) and start_line > 0 else 1
        end = end_line if isinstance(end_line, int) and end_line >= start else start + 119
        lines = target.read_text().splitlines()
        if start > len(lines):
            return "error", f"start_line {start} is beyond the end of {path}"
        selected = lines[start - 1 : end]
        rendered = [f"{index}: {line}" for index, line in enumerate(selected, start=start)]
        return "completed", "\n".join(rendered)

    def _search_text(self, query: str, path: str, max_results: object) -> tuple[str, str]:
        normalized = query.strip()
        if not normalized:
            return "error", "search_text requires a non-empty query."
        target = self._resolve_path(path, mode="list") if path.strip() else self.project_root
        if not target.exists():
            return "error", f"Search path does not exist: {path or self.project_root.as_posix()}"
        limit = max_results if isinstance(max_results, int) and max_results > 0 else 40
        rg_bin = shutil.which("rg")
        if rg_bin:
            command = [rg_bin, "-n", "--no-heading", "--color", "never", normalized, str(target)]
            result = subprocess.run(command, capture_output=True, text=True, check=False, timeout=20)
            output = (result.stdout or result.stderr).strip()
            if not output:
                return "completed", f"No matches found for query: {normalized}"
            return "completed", "\n".join(output.splitlines()[:limit])
        return self._search_text_fallback(normalized, target, limit)

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

    def _show_status(self) -> tuple[str, str]:
        result = subprocess.run(
            ["git", "status", "--short"],
            cwd=self.project_root,
            capture_output=True,
            text=True,
            check=False,
            timeout=20,
        )
        if result.returncode != 0:
            output = (result.stdout or result.stderr).strip() or "Not a Git repository."
            return "error", output
        output = result.stdout.strip()
        return "completed", output or "Working tree is clean."

    def _show_diff(self, path: str) -> tuple[str, str]:
        target = self._resolve_path(path, mode="read") if path.strip() else self.project_root
        if not workspace_utils.path_within(self.project_root, target):
            return "error", "show_diff only supports paths inside the current session workspace."
        relative_path = None
        if target != self.project_root:
            relative_path = target.relative_to(self.project_root).as_posix()
        command = ["git", "diff", "--no-ext-diff", "HEAD", "--"]
        if relative_path:
            command.append(relative_path)
        result = subprocess.run(
            command,
            cwd=self.project_root,
            capture_output=True,
            text=True,
            check=False,
            timeout=20,
        )
        if result.returncode != 0:
            output = (result.stdout or result.stderr).strip() or "Unable to compute diff."
            return "error", output
        output = result.stdout.strip()
        return "completed", (output[:20000] if output else "No diff from HEAD.")

    def _run_test(self, argv: object) -> ToolExecutionResult:
        if not isinstance(argv, list) or not argv:
            return ToolExecutionResult(status="error", output="run_test requires a non-empty argv list.")
        command = [str(item).strip() for item in argv if str(item).strip()]
        if not command:
            return ToolExecutionResult(status="error", output="run_test requires a non-empty argv list.")
        original_command = list(command)
        command = self._normalize_test_command(command)
        target_python = self._workspace_python_executable()
        command, python_executable = self._rewrite_test_command_for_workspace(command, target_python)
        classification, verification_kind, evidence_strength = self._classify_run_test_command(command)
        payload = {
            "original_command": original_command,
            "resolved_command": command,
            "classification": classification,
            "verification_kind": verification_kind,
            "evidence_strength": evidence_strength,
            "target_python_executable": target_python.as_posix() if target_python else None,
            "python_executable": python_executable.as_posix() if python_executable else None,
            "used_workspace_venv": bool(
                target_python and python_executable and python_executable.resolve() == target_python.resolve()
            ),
            "wrong_environment": bool(
                target_python and python_executable and python_executable.resolve() != target_python.resolve()
            ),
        }
        if classification == "blocked_mutation":
            command_preview = " ".join(original_command[:6])
            return ToolExecutionResult(
                status="blocked",
                output=(
                    "run_test only supports verification or read-only inspection commands. "
                    f"Use bash with approval for side-effecting commands such as `{command_preview}`."
                ),
                payload=payload,
            )
        result = subprocess.run(
            command,
            cwd=self.project_root,
            capture_output=True,
            text=True,
            check=False,
            timeout=120,
        )
        combined = "\n".join(
            part for part in [result.stdout.strip(), result.stderr.strip()] if part
        ).strip()
        output = combined[:20000] if combined else "(no output)"
        prefix = f"exit_code={result.returncode}"
        return ToolExecutionResult(
            status="completed" if result.returncode == 0 else "error",
            output=f"{prefix}\n{output}",
            payload=payload,
        )

    def _apply_patch(self, patch_text: str) -> tuple[str, str]:
        if not self.write_enabled:
            return "blocked", "This session is in Default Conversations mode. Bind it to a workspace before applying patches."
        normalized = patch_text.strip()
        if not normalized:
            return "error", "apply_patch requires a non-empty patch."
        if normalized.startswith("*** Begin Patch"):
            return self._apply_structured_patch(normalized)
        repo_root = self._git_repo_root()
        if repo_root is None:
            return "error", "apply_patch requires the current workspace to be inside a Git repository."
        extracted_paths = self._extract_patch_paths(normalized)
        if not extracted_paths:
            return "error", "apply_patch could not determine any patch targets."
        for relative_path in extracted_paths:
            candidate = (repo_root / relative_path).resolve()
            if not workspace_utils.path_within(self.project_root, candidate):
                return "blocked", f"Patch target is outside the current session workspace: {relative_path}"
        with tempfile.NamedTemporaryFile("w", encoding="utf-8", delete=False) as handle:
            handle.write(normalized + "\n")
            temp_path = Path(handle.name)
        try:
            check = subprocess.run(
                ["git", "apply", "--check", "--whitespace=nowarn", temp_path.as_posix()],
                cwd=repo_root,
                capture_output=True,
                text=True,
                check=False,
                timeout=20,
            )
            if check.returncode != 0:
                output = (check.stdout or check.stderr).strip() or "Patch check failed."
                return "error", output
            apply = subprocess.run(
                ["git", "apply", "--whitespace=nowarn", temp_path.as_posix()],
                cwd=repo_root,
                capture_output=True,
                text=True,
                check=False,
                timeout=20,
            )
            if apply.returncode != 0:
                output = (apply.stdout or apply.stderr).strip() or "Patch apply failed."
                return "error", output
            return "completed", f"Applied patch touching {len(extracted_paths)} file(s)."
        finally:
            try:
                temp_path.unlink(missing_ok=True)
            except Exception:
                pass

    def _run_bash(self, command: str) -> tuple[str, str]:
        if not command.strip():
            return "error", "bash requires a command after 'bash:'."
        banned = ["rm -rf", "sudo", "shutdown", "reboot"]
        if any(token in command for token in banned):
            return "blocked", "Blocked potentially destructive command."
        normalized_argv = self._normalize_bash_command(command)
        timeout_seconds = 300 if self._looks_like_package_install(command, normalized_argv) else 120
        if normalized_argv is None:
            result = subprocess.run(
                command,
                shell=True,
                cwd=self.project_root,
                capture_output=True,
                text=True,
                timeout=timeout_seconds,
            )
        else:
            result = subprocess.run(
                normalized_argv,
                shell=False,
                cwd=self.project_root,
                capture_output=True,
                text=True,
                timeout=timeout_seconds,
            )
        output = (result.stdout + result.stderr).strip()[:10000] or "(no output)"
        output = f"exit_code={result.returncode}\n{output}"
        return ("completed" if result.returncode == 0 else "error"), output

    def _search_text_fallback(self, query: str, root: Path, limit: int) -> tuple[str, str]:
        matches: list[str] = []
        for file_path in sorted(self._iter_visible_files(root)):
            try:
                lines = file_path.read_text().splitlines()
            except Exception:
                continue
            for index, line in enumerate(lines, start=1):
                if query not in line:
                    continue
                relative = file_path.relative_to(root).as_posix() if root.is_dir() else file_path.name
                matches.append(f"{relative}:{index}:{line}")
                if len(matches) >= limit:
                    return "completed", "\n".join(matches)
        if not matches:
            return "completed", f"No matches found for query: {query}"
        return "completed", "\n".join(matches)

    def _normalize_test_command(self, command: list[str]) -> list[str]:
        if not command:
            return command
        if command[0] == "python" and shutil.which("python") is None and shutil.which("python3"):
            normalized = list(command)
            normalized[0] = "python3"
            return normalized
        return command

    def _normalize_bash_command(self, command: str) -> list[str] | None:
        if self._shell_command_uses_metacharacters(command):
            return None
        try:
            argv = shlex.split(command)
        except ValueError:
            return None
        if not argv:
            return None
        normalized = self._normalize_test_command(argv)
        target_python = self._workspace_python_executable()
        rewritten, _python_executable = self._rewrite_test_command_for_workspace(normalized, target_python)
        return rewritten

    def _shell_command_uses_metacharacters(self, command: str) -> bool:
        return bool(re.search(r"[|&;<>$`\\\n]", command))

    def _looks_like_package_install(self, command: str, argv: list[str] | None) -> bool:
        normalized = command.lower()
        if re.search(r"(^|\s)(pip|pip3)\s+install(\s|$)", normalized):
            return True
        if any(token in normalized for token in ("poetry add ", "npm install ", "uv pip install ")):
            return True
        if not argv:
            return False
        classification, verification_kind, _evidence_strength = self._classify_run_test_command(argv)
        return classification == "blocked_mutation" and verification_kind == "package_install"

    def _workspace_python_executable(self) -> Path | None:
        candidate = self.project_root / ".venv" / "bin" / "python"
        if candidate.exists() and candidate.is_file():
            return candidate
        return None

    def _rewrite_test_command_for_workspace(
        self,
        command: list[str],
        target_python: Path | None,
    ) -> tuple[list[str], Path | None]:
        if not command:
            return command, None
        first = command[0]
        if target_python and first in {"python", "python3"}:
            rewritten = list(command)
            rewritten[0] = target_python.as_posix()
            return rewritten, target_python
        if target_python and first in {"pip", "pip3"}:
            rewritten = [target_python.as_posix(), "-m", "pip", *command[1:]]
            return rewritten, target_python
        first_name = Path(first).name
        if first_name in {"python", "python3"}:
            return command, Path(first)
        return command, None

    def _classify_run_test_command(self, command: list[str]) -> tuple[str, str | None, str | None]:
        if not command:
            return "inspection", None, None
        first = Path(command[0]).name.lower()
        if first in {"python", "python3"}:
            return self._classify_python_run_test(command)
        if first in {"pip", "pip3"}:
            action = command[1].lower() if len(command) > 1 else ""
            if action in {"install", "uninstall", "download", "wheel"}:
                return "blocked_mutation", "package_install", None
            if action in {"show", "list", "freeze", "check"}:
                return "verification", "package_probe", "sufficient"
            return "inspection", "package_inspection", None
        if first == "npm":
            action = command[1].lower() if len(command) > 1 else ""
            if action in {"install", "add", "remove", "update"}:
                return "blocked_mutation", "package_install", None
            if action in {"test", "run"}:
                return "verification", "test_run", "sufficient"
        if first in {"pnpm", "yarn"}:
            action = command[1].lower() if len(command) > 1 else ""
            if action in {"install", "add", "remove", "update"}:
                return "blocked_mutation", "package_install", None
        if first == "poetry":
            action = command[1].lower() if len(command) > 1 else ""
            if action in {"install", "add", "remove", "update", "sync"}:
                return "blocked_mutation", "package_install", None
            if action in {"run", "show"}:
                return "verification", "package_probe", "sufficient"
        if first == "uv":
            action = command[1].lower() if len(command) > 1 else ""
            if action in {"sync", "add", "remove"}:
                return "blocked_mutation", "package_install", None
            if action == "pip":
                pip_action = command[2].lower() if len(command) > 2 else ""
                if pip_action in {"install", "uninstall"}:
                    return "blocked_mutation", "package_install", None
                if pip_action in {"show", "list", "freeze", "check"}:
                    return "verification", "package_probe", "sufficient"
        if first == "git":
            action = command[1].lower() if len(command) > 1 else ""
            if action in {"status", "diff", "show", "log", "rev-parse", "branch"}:
                return "inspection", "git_inspection", None
        if first in {"ls", "cat", "find", "rg", "grep", "pwd", "which", "echo"}:
            return "inspection", "read_only_command", None
        return "verification", "command_execution", "sufficient"

    def _classify_python_run_test(self, command: list[str]) -> tuple[str, str | None, str | None]:
        if len(command) >= 3 and command[1] == "-m":
            module = command[2].lower()
            action = command[3].lower() if len(command) > 3 else ""
            if module == "pip":
                if action in {"install", "uninstall", "download", "wheel"}:
                    return "blocked_mutation", "package_install", None
                if action in {"show", "list", "freeze", "check"}:
                    return "verification", "package_probe", "sufficient"
                return "inspection", "package_inspection", None
            if module in {"py_compile", "compileall"}:
                return "verification", "syntax_check", "weak"
            if module in {"pytest", "unittest"}:
                return "verification", "test_run", "sufficient"
            return "verification", "module_run", "sufficient"
        if len(command) >= 2 and command[1] == "-c":
            return "verification", "python_inline", "sufficient"
        return "verification", "script_run", "sufficient"

    def _apply_structured_patch(self, patch_text: str) -> tuple[str, str]:
        lines = patch_text.splitlines()
        if not lines or lines[0].strip() != "*** Begin Patch":
            return "error", "Structured patch is missing '*** Begin Patch'."
        if lines[-1].strip() != "*** End Patch":
            return "error", "Structured patch is missing '*** End Patch'."

        index = 1
        touched = 0
        while index < len(lines) - 1:
            line = lines[index]
            if line.startswith("*** Update File: "):
                path = line[len("*** Update File: "):].strip()
                index += 1
                section_lines: list[str] = []
                while index < len(lines) - 1 and not lines[index].startswith("*** "):
                    section_lines.append(lines[index])
                    index += 1
                status, output = self._apply_structured_update(path, section_lines)
                if status != "completed":
                    return status, output
                touched += 1
                continue
            if line.startswith("*** Add File: "):
                path = line[len("*** Add File: "):].strip()
                index += 1
                section_lines: list[str] = []
                while index < len(lines) - 1 and not lines[index].startswith("*** "):
                    section_lines.append(lines[index])
                    index += 1
                status, output = self._apply_structured_add(path, section_lines)
                if status != "completed":
                    return status, output
                touched += 1
                continue
            if line.startswith("*** Delete File: "):
                path = line[len("*** Delete File: "):].strip()
                status, output = self._apply_structured_delete(path)
                if status != "completed":
                    return status, output
                touched += 1
                index += 1
                continue
            return "error", f"Unsupported structured patch directive: {line}"
        if touched == 0:
            return "error", "Structured patch did not contain any file changes."
        return "completed", f"Applied patch touching {touched} file(s)."

    def _apply_structured_update(self, path: str, section_lines: list[str]) -> tuple[str, str]:
        target = self._safe_path(path, mode="write")
        if not target.exists():
            return "error", f"File not found: {path}"
        current = target.read_text()
        try:
            hunks = self._parse_structured_hunks(section_lines)
        except ValueError as exc:
            return "error", str(exc)
        if not hunks:
            return "error", f"Structured patch for {path} did not contain any hunks."
        updated = current
        for old_text, new_text in hunks:
            if old_text not in updated:
                return "error", f"Structured patch context not found in {path}"
            updated = updated.replace(old_text, new_text, 1)
        target.write_text(updated)
        return "completed", f"Updated {path}"

    def _apply_structured_add(self, path: str, section_lines: list[str]) -> tuple[str, str]:
        target = self._safe_path(path, mode="write")
        if target.exists():
            return "error", f"File already exists: {path}"
        content_lines = []
        for line in section_lines:
            if not line.startswith("+"):
                return "error", f"Structured add file only supports '+' lines: {line}"
            content_lines.append(line[1:])
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("\n".join(content_lines) + ("\n" if content_lines else ""))
        return "completed", f"Added {path}"

    def _apply_structured_delete(self, path: str) -> tuple[str, str]:
        target = self._safe_path(path, mode="write")
        if not target.exists():
            return "error", f"File not found: {path}"
        if target.is_dir():
            return "error", f"Path is a directory, not a file: {path}"
        target.unlink()
        return "completed", f"Deleted {path}"

    def _parse_structured_hunks(self, section_lines: list[str]) -> list[tuple[str, str]]:
        hunks: list[tuple[str, str]] = []
        current: list[str] = []
        for line in section_lines:
            if line == "@@":
                if current:
                    parsed = self._structured_hunk_to_texts(current)
                    if isinstance(parsed, tuple):
                        hunks.append(parsed)
                    current = []
                continue
            current.append(line)
        if current:
            parsed = self._structured_hunk_to_texts(current)
            if isinstance(parsed, tuple):
                hunks.append(parsed)
        return hunks

    def _structured_hunk_to_texts(self, lines: list[str]) -> tuple[str, str]:
        old_lines: list[str] = []
        new_lines: list[str] = []
        for line in lines:
            if not line:
                old_lines.append("")
                new_lines.append("")
                continue
            prefix = line[0]
            body = line[1:]
            if prefix == " ":
                old_lines.append(body)
                new_lines.append(body)
                continue
            if prefix == "-":
                old_lines.append(body)
                continue
            if prefix == "+":
                new_lines.append(body)
                continue
            raise ValueError(f"Unsupported structured patch hunk line: {line}")
        return "\n".join(old_lines), "\n".join(new_lines)

    def _git_repo_root(self) -> Path | None:
        result = subprocess.run(
            ["git", "rev-parse", "--show-toplevel"],
            cwd=self.project_root,
            capture_output=True,
            text=True,
            check=False,
            timeout=20,
        )
        if result.returncode != 0:
            return None
        output = (result.stdout or result.stderr).strip()
        if not output:
            return None
        path = Path(output).resolve()
        return path if path.exists() and path.is_dir() else None

    def _extract_patch_paths(self, patch_text: str) -> list[Path]:
        paths: list[Path] = []
        seen: set[str] = set()
        for line in patch_text.splitlines():
            if not line.startswith("+++ b/"):
                continue
            raw = line[6:].strip()
            if not raw or raw == "/dev/null":
                continue
            normalized = raw.split("\t", 1)[0].strip()
            key = normalized
            if key in seen:
                continue
            seen.add(key)
            paths.append(Path(normalized))
        if paths:
            return paths
        for match in re.finditer(r"diff --git a/(.+?) b/(.+)", patch_text):
            normalized = match.group(2).strip()
            if not normalized or normalized == "/dev/null" or normalized in seen:
                continue
            seen.add(normalized)
            paths.append(Path(normalized))
        return paths

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
