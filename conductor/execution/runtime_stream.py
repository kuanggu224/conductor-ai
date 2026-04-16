"""Runtime stream store for live CLI output."""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
from threading import Condition
from typing import Deque


@dataclass(slots=True)
class RuntimeStreamSnapshot:
    """A serializable snapshot of the current runtime stream."""

    project_id: str
    running: bool = False
    status: str = "idle"
    workitem_id: str = ""
    agent_role: str = ""
    backend: str = ""
    cli_name: str = ""
    lines: list[str] = field(default_factory=list)
    version: int = 0


class RuntimeStreamStore:
    """Thread-safe in-memory stream buffer keyed by project."""

    def __init__(self, max_lines: int = 200) -> None:
        self.max_lines = max_lines
        self._condition = Condition()
        self._streams: dict[str, RuntimeStreamSnapshot] = {}

    def start(
        self,
        project_id: str,
        workitem_id: str,
        agent_role: str,
        backend: str,
        cli_name: str,
    ) -> None:
        """Reset and mark a project stream as running."""
        with self._condition:
            previous = self._streams.get(project_id)
            version = previous.version + 1 if previous else 1
            self._streams[project_id] = RuntimeStreamSnapshot(
                project_id=project_id,
                running=True,
                status="running",
                workitem_id=workitem_id,
                agent_role=agent_role,
                backend=backend,
                cli_name=cli_name,
                lines=[],
                version=version,
            )
            self._condition.notify_all()

    def append(self, project_id: str, channel: str, line: str) -> None:
        """Append one stdout/stderr line to the stream."""
        cleaned = line.rstrip("\r\n")
        if not cleaned:
            return
        with self._condition:
            snapshot = self._streams.setdefault(project_id, RuntimeStreamSnapshot(project_id=project_id))
            buffer: Deque[str] = deque(snapshot.lines, maxlen=self.max_lines)
            prefix = "stderr | " if channel == "stderr" else ""
            buffer.append(f"{prefix}{cleaned}")
            snapshot.lines = list(buffer)
            snapshot.version += 1
            self._condition.notify_all()

    def finish(self, project_id: str, success: bool, message: str | None = None) -> None:
        """Mark the project stream as finished and optionally append a final line."""
        with self._condition:
            snapshot = self._streams.setdefault(project_id, RuntimeStreamSnapshot(project_id=project_id))
            snapshot.running = False
            snapshot.status = "completed" if success else "failed"
            if message:
                buffer: Deque[str] = deque(snapshot.lines, maxlen=self.max_lines)
                buffer.append(message)
                snapshot.lines = list(buffer)
            snapshot.version += 1
            self._condition.notify_all()

    def snapshot(self, project_id: str) -> RuntimeStreamSnapshot:
        """Return the current snapshot for a project."""
        with self._condition:
            snapshot = self._streams.get(project_id)
            if snapshot is None:
                return RuntimeStreamSnapshot(project_id=project_id)
            return RuntimeStreamSnapshot(
                project_id=snapshot.project_id,
                running=snapshot.running,
                status=snapshot.status,
                workitem_id=snapshot.workitem_id,
                agent_role=snapshot.agent_role,
                backend=snapshot.backend,
                cli_name=snapshot.cli_name,
                lines=list(snapshot.lines),
                version=snapshot.version,
            )

    def wait_for_update(self, project_id: str, last_version: int, timeout_seconds: float = 1.0) -> RuntimeStreamSnapshot:
        """Block until a new snapshot version is available or timeout expires."""
        with self._condition:
            self._condition.wait_for(
                lambda: self._streams.get(project_id, RuntimeStreamSnapshot(project_id=project_id)).version > last_version,
                timeout=timeout_seconds,
            )
            return self.snapshot(project_id)
