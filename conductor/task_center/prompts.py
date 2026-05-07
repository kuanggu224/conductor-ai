"""Task Center prompt file helpers."""

from __future__ import annotations

from pathlib import Path


def resolve_task_prompt_path(prompt_file: str, project_root: str) -> Path:
    """Resolve a prompt path and require it to stay inside the project root."""
    root = Path(project_root or ".").expanduser().resolve()
    path = Path(prompt_file).expanduser()
    if not path.is_absolute():
        path = root / path
    resolved = path.resolve()
    try:
        resolved.relative_to(root)
    except ValueError as error:
        raise ValueError(f"Prompt file must be inside project root: {resolved}") from error
    return resolved


def write_task_prompt_file(prompt_file: str, project_root: str, content: str) -> Path:
    """Write a rendered task prompt under the project root and return its path."""
    path = resolve_task_prompt_path(prompt_file, project_root)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    return path


__all__ = ["resolve_task_prompt_path", "write_task_prompt_file"]
