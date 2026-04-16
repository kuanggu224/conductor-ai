"""Artifact 文件存储。"""

from __future__ import annotations

import re
from dataclasses import replace
from pathlib import Path

from conductor.domain.models import Artifact


class ArtifactStore:
    """把文档型 Artifact 保存为 Markdown 文件。"""

    def __init__(self, root_dir: str | Path = ".conductor_artifacts") -> None:
        self.root_dir = Path(root_dir)

    def save_markdown(self, artifact: Artifact, project_root: str | Path | None = None) -> Artifact:
        """保存 Markdown 产物并返回带 path 的 Artifact。"""
        project_dir = self._resolve_root_dir(project_root) / self._safe_filename(artifact.project_id)
        project_dir.mkdir(parents=True, exist_ok=True)
        path = project_dir / f"{self._safe_filename(artifact.id)}.md"
        path.write_text(self._build_markdown(artifact), encoding="utf-8")
        return replace(artifact, path=str(path))

    def read_content(self, artifact: Artifact) -> str:
        """从文件读取产物内容；无 path 时回退到内存内容。"""
        if not artifact.path:
            return artifact.content
        path = Path(artifact.path)
        if not path.exists():
            return artifact.content
        return path.read_text(encoding="utf-8")

    def _build_markdown(self, artifact: Artifact) -> str:
        """构建保存到磁盘的 Markdown 内容。"""
        return (
            f"# {artifact.title}\n\n"
            f"- Artifact ID: `{artifact.id}`\n"
            f"- Project ID: `{artifact.project_id}`\n"
            f"- WorkItem ID: `{artifact.workitem_id}`\n"
            f"- Agent ID: `{artifact.agent_id}`\n"
            f"- Kind: `{artifact.kind}`\n"
            f"- Source Backend: `{artifact.source_backend}`\n\n"
            f"- Parent Artifact ID: `{artifact.parent_artifact_id or '-'}`\n"
            f"- Review Of: `{artifact.review_of or '-'}`\n"
            f"- Version: `{artifact.version}`\n"
            f"- Collaboration Session ID: `{artifact.collaboration_session_id or '-'}`\n"
            f"- Derived From: `{', '.join(artifact.derived_from) if artifact.derived_from else '-'}`\n\n"
            f"{artifact.content}\n"
        )

    def _safe_filename(self, value: str) -> str:
        """转换为安全文件名。"""
        cleaned = re.sub(r"[^A-Za-z0-9_.-]+", "-", value).strip("-")
        return cleaned or "artifact"

    def _resolve_root_dir(self, project_root: str | Path | None) -> Path:
        """Resolve artifact root for a specific project."""
        if project_root:
            return Path(project_root) / ".conductor" / "artifacts"
        return self.root_dir
