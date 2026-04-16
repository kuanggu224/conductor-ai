"""最小 JSONL 日志存储。"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path


@dataclass(slots=True)
class ProjectLogEntry:
    """项目日志条目。"""

    timestamp: str
    project_id: str
    event_index: int
    message: str


class ProjectLogStore:
    """按项目写入 JSONL 日志文件。"""

    def __init__(self, root_dir: str | Path) -> None:
        self.root_dir = Path(root_dir)
        self.root_dir.mkdir(parents=True, exist_ok=True)

    def append_event(self, project_id: str, event_index: int, message: str, project_root: str | Path | None = None) -> None:
        """向指定项目日志追加一条事件。"""
        entry = ProjectLogEntry(
            timestamp=datetime.now(timezone.utc).isoformat(),
            project_id=project_id,
            event_index=event_index,
            message=message,
        )
        with self._project_log_path(project_id, project_root).open("a", encoding="utf-8") as file:
            file.write(json.dumps(asdict(entry), ensure_ascii=False) + "\n")

    def read_events(self, project_id: str, project_root: str | Path | None = None) -> list[ProjectLogEntry]:
        """读取指定项目的全部日志。"""
        path = self._project_log_path(project_id, project_root)
        if not path.exists():
            return []
        entries: list[ProjectLogEntry] = []
        with path.open("r", encoding="utf-8") as file:
            for line in file:
                payload = json.loads(line)
                entries.append(ProjectLogEntry(**payload))
        return entries

    def _project_log_path(self, project_id: str, project_root: str | Path | None = None) -> Path:
        """返回项目日志文件路径。"""
        root = (Path(project_root) / ".conductor" / "logs") if project_root else self.root_dir
        root.mkdir(parents=True, exist_ok=True)
        return root / f"{project_id}.jsonl"
