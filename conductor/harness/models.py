"""Harness 协议模型。"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable


StreamCallback = Callable[[str, str], None]


@dataclass(slots=True)
class HarnessRequest:
    """一次受控执行请求。"""

    command: list[str]
    working_directory: str
    timeout_seconds: float = 120.0
    environment: dict[str, str] = field(default_factory=dict)
    description: str = ""
    track_workspace_changes: bool = False
    workspace_root: str | None = None
    stream_callback: StreamCallback | None = None


@dataclass(slots=True)
class HarnessResult:
    """一次受控执行结果。"""

    success: bool
    exit_code: int
    stdout: str
    stderr: str
    duration_ms: int
    changed_files: list[str] = field(default_factory=list)
    artifacts: list[str] = field(default_factory=list)
