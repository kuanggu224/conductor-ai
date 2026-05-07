"""ConductorEngine 测试。"""

from conductor.agents.llm import LLMHTTPConfig
from conductor.config.cli import CLISelectionConfig
from conductor.config.execution import ExecutionScopeConfig
from conductor.config.llm import LLMRuntimeConfig, LLMUsagePolicy
from conductor.controller.engine import ConductorEngine


def build_test_llm_config() -> LLMRuntimeConfig:
    return LLMRuntimeConfig(
        local=LLMHTTPConfig(base_url="http://127.0.0.1:11434/v1", model_name="local-test", enabled=False),
        cloud=LLMHTTPConfig(base_url="https://api.example.com/v1", model_name="cloud-test", enabled=False),
        usage=LLMUsagePolicy(runner_enabled=False),
    )


def test_engine_can_create_list_and_get_projects(tmp_path) -> None:
    engine = ConductorEngine(
        log_dir=tmp_path / "logs",
        artifact_dir=tmp_path / "artifacts",
        llm_runtime_config=build_test_llm_config(),
        execution_scope_config=ExecutionScopeConfig(),
        cli_selection_config=CLISelectionConfig(),
    )

    created = engine.create_project("实现一个包含 API 和测试的功能")
    listed = engine.list_projects()
    loaded = engine.get_project(created.project.id)

    assert len(listed) == 1
    assert listed[0].project.id == created.project.id
    assert loaded.project.goal == "实现一个包含 API 和测试的功能"
    assert engine.read_project_logs(created.project.id)


def test_engine_can_run_project_to_terminal_state(tmp_path) -> None:
    engine = ConductorEngine(
        log_dir=tmp_path / "logs",
        artifact_dir=tmp_path / "artifacts",
        llm_runtime_config=build_test_llm_config(),
        execution_scope_config=ExecutionScopeConfig(),
        cli_selection_config=CLISelectionConfig(),
    )
    created = engine.create_project("实现最小骨架")

    final_state = engine.run_project(created.project.id)

    assert final_state.project_status.value in {"completed", "blocked"}
    assert final_state.artifacts
    assert any(artifact.kind == "collaboration_review" for artifact in final_state.artifacts)
    assert final_state.collaborations
    assert final_state.artifacts[0].path
    logs = engine.read_project_logs(created.project.id)
    assert (
        logs[-1].message
        in {"Project 已完成", "升级处理: 阶段 design 存在超过重试次数的失败 WorkItem: workitem-fail"}
        or logs[-1].message.startswith("需求门禁返工上限触发")
    )


def test_engine_can_create_project_in_selected_directory(tmp_path) -> None:
    engine = ConductorEngine(
        log_dir=tmp_path / "global-logs",
        artifact_dir=tmp_path / "global-artifacts",
        llm_runtime_config=build_test_llm_config(),
        execution_scope_config=ExecutionScopeConfig(),
        cli_selection_config=CLISelectionConfig(),
    )
    project_root = tmp_path / "selected-project-root"

    created = engine.create_project("实现最小骨架", project_root=str(project_root))
    final_state = engine.run_project(created.project.id)

    assert created.project.project_root == str(project_root.resolve())
    assert final_state.project.project_root == str(project_root.resolve())
    assert final_state.artifacts
    assert final_state.artifacts[0].path is not None
    assert str(project_root / ".conductor" / "artifacts") in final_state.artifacts[0].path
    log_path = project_root / ".conductor" / "logs" / f"{created.project.id}.jsonl"
    assert log_path.exists()
