"""Log persistence tests."""

from conductor.config.cli import CLISelectionConfig
from conductor.controller.engine import ConductorEngine
from conductor.logging.store import ProjectLogStore


def test_project_log_store_can_append_and_read_entries(tmp_path) -> None:
    store = ProjectLogStore(tmp_path)

    store.append_event("project-1", 0, "项目已创建")
    store.append_event("project-1", 1, "进入阶段 design")
    entries = store.read_events("project-1")

    assert len(entries) == 2
    assert entries[0].project_id == "project-1"
    assert entries[1].message == "进入阶段 design"
    assert entries[1].event_type == "stage_transition"


def test_project_log_store_writes_structured_state_events_and_report(tmp_path) -> None:
    engine = ConductorEngine(
        log_dir=tmp_path / "logs",
        artifact_dir=tmp_path / "artifacts",
        cli_selection_config=CLISelectionConfig(),
    )
    state = engine.create_project("实现一个 API 和 UI")
    state = engine.step_project(state.project.id)

    entries = engine.read_project_logs(state.project.id)
    report_path = engine.write_project_report(state.project.id)

    assert entries
    assert entries[0].event_type in {"event", "stage_transition", "agent_activation"}
    assert entries[0].stage
    assert entries[0].project_status
    assert entries[0].metadata is not None
    assert "workitem_counts" in entries[0].metadata
    assert report_path.exists()
    report = report_path.read_text(encoding="utf-8")
    assert "## Activated Agents" in report
    assert "## Event Timeline" in report
