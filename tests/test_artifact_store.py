"""ArtifactStore 测试。"""

from pathlib import Path

from conductor.artifacts.store import ArtifactStore
from conductor.domain.models import Artifact


def test_artifact_store_saves_markdown_file(tmp_path) -> None:
    store = ArtifactStore(tmp_path)
    artifact = Artifact(
        id="artifact-workitem-1",
        project_id="project-1",
        workitem_id="workitem-1",
        agent_id="agent-designer",
        kind="design_overview",
        title="产品/设计文档",
        content="设计内容",
        source_backend="llm/cloud",
        parent_artifact_id="artifact-root",
        derived_from=["artifact-root"],
        review_of="artifact-root",
        version=2,
        collaboration_session_id="collaboration-1",
    )

    saved = store.save_markdown(artifact)

    assert saved.path is not None
    path = Path(saved.path)
    assert path.exists()
    assert path.read_text(encoding="utf-8").startswith("# 产品/设计文档")
    assert "Source Backend: `llm/cloud`" in path.read_text(encoding="utf-8")
    assert "Parent Artifact ID: `artifact-root`" in path.read_text(encoding="utf-8")
    assert "Version: `2`" in path.read_text(encoding="utf-8")
    assert "Artifact Contract" in path.read_text(encoding="utf-8")
    assert "Missing Sections" in path.read_text(encoding="utf-8")
    assert "设计内容" in store.read_content(saved)


def test_artifact_store_uses_project_directory_when_provided(tmp_path) -> None:
    store = ArtifactStore(tmp_path / "fallback-artifacts")
    project_root = tmp_path / "demo-project"
    artifact = Artifact(
        id="artifact-workitem-2",
        project_id="project-2",
        workitem_id="workitem-2",
        agent_id="agent-backend",
        kind="api_implementation",
        title="后端实现说明",
        content="实现内容",
        source_backend="agent_cli/codex",
    )

    saved = store.save_markdown(artifact, project_root=project_root)

    assert saved.path is not None
    path = Path(saved.path)
    assert path.exists()
    assert path.parent == project_root / ".conductor" / "artifacts" / "project-2"
