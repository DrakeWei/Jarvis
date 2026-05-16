from __future__ import annotations

import hashlib
import re
from pathlib import Path

from app.core.config import settings

_PATH_PATTERN = re.compile(r"(/[^ \t\r\n，。；！？,;:'\"`]+)")


def normalize_workspace_path(value: str | Path | None) -> Path:
    raw = Path(value).expanduser() if value else settings.project_root
    path = raw.resolve()
    if not path.exists() or not path.is_dir():
        raise ValueError(f"Workspace does not exist or is not a directory: {raw}")
    return path


def workspace_label(path: str | Path) -> str:
    resolved = Path(path).resolve()
    return resolved.name or resolved.as_posix()


def workspace_fingerprint(path: str | Path) -> str:
    resolved = Path(path).resolve()
    digest = hashlib.sha1(resolved.as_posix().encode("utf-8")).hexdigest()
    return digest[:16]


def explicit_paths_from_text(content: str) -> list[Path]:
    found: list[Path] = []
    seen: set[str] = set()
    for match in _PATH_PATTERN.findall(content):
        candidate = match.rstrip(".,;:!?)]}，。；！？")
        path = Path(candidate).expanduser()
        try:
            resolved = path.resolve()
        except Exception:
            continue
        if not resolved.exists():
            continue
        key = resolved.as_posix()
        if key in seen:
            continue
        seen.add(key)
        found.append(resolved)
    return found


def path_within(parent: Path, candidate: Path) -> bool:
    try:
        candidate.resolve().relative_to(parent.resolve())
        return True
    except ValueError:
        return False


def candidate_workspace_paths() -> list[Path]:
    candidates = [settings.project_root]
    projects = settings.codex_config.get("projects")
    if isinstance(projects, dict):
        for raw_path in projects.keys():
            path = Path(str(raw_path)).expanduser()
            if path.exists() and path.is_dir():
                candidates.append(path.resolve())

    parent = settings.project_root.parent
    if parent.exists():
        for child in parent.iterdir():
            if child.is_dir():
                candidates.append(child.resolve())

    unique: list[Path] = []
    seen: set[str] = set()
    for path in candidates:
        key = path.as_posix()
        if key in seen:
            continue
        seen.add(key)
        unique.append(path)
    return unique


def detect_named_workspace_reference(content: str, current_workspace: Path) -> Path | None:
    normalized = content.lower()
    for path in sorted(candidate_workspace_paths(), key=lambda item: len(item.name), reverse=True):
        if path == current_workspace:
            continue
        name = path.name.lower()
        escaped = re.escape(name)
        explicit_patterns = [
            rf"(?:在|到|进入|切换到|绑定到|使用)\s*[`\"'“”]?{escaped}[`\"'“”]?\s*(?:项目|目录|文件夹|工程|workspace|folder|project)",
            rf"(?:项目|目录|文件夹|工程|workspace|folder|project)\s*[`\"'“”]?{escaped}[`\"'“”]?",
            rf"[`\"'“”]{escaped}[`\"'“”]\s*(?:项目|目录|文件夹|工程|workspace|folder|project)?",
        ]
        if any(re.search(pattern, normalized, flags=re.IGNORECASE) for pattern in explicit_patterns):
            return path
    return None


def resolve_workspace_from_text(content: str, default_workspace: Path | None = None) -> Path | None:
    current_workspace = default_workspace.resolve() if default_workspace else settings.project_root
    explicit = explicit_paths_from_text(content)
    for path in explicit:
        if path.is_dir():
            return path
        if path.is_file():
            return path.parent.resolve()
    return detect_named_workspace_reference(content, current_workspace)
