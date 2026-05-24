"""执行范围配置测试。"""

from conductor.config.execution import ExecutionScopeConfig, load_execution_scope_config, resolve_run_profile, save_execution_scope_config
from conductor.domain.models import Stage
from conductor.execution.planner import Planner


def test_execution_scope_config_filters_workitem_kinds() -> None:
    config = ExecutionScopeConfig(
        requirement_design_enabled=True,
        design_detail_enabled=False,
        backend_development_enabled=True,
        frontend_development_enabled=False,
        testing_enabled=False,
    )

    assert config.is_workitem_kind_enabled("design_overview") is True
    assert config.is_workitem_kind_enabled("api_design") is False
    assert config.is_workitem_kind_enabled("api_implementation") is True
    assert config.is_workitem_kind_enabled("ui_implementation") is False
    assert config.is_workitem_kind_enabled("acceptance_check") is False


def test_planner_respects_execution_scope_config() -> None:
    planner = Planner(
        scope_config=ExecutionScopeConfig(
            design_detail_enabled=False,
            frontend_development_enabled=False,
            testing_enabled=False,
        )
    )

    design_items = planner.plan_stage_workitems(Stage("design", "", ""), "实现 API UI 测试")
    development_items = planner.plan_stage_workitems(Stage("development", "", ""), "实现 API UI 测试")
    testing_items = planner.plan_stage_workitems(Stage("testing", "", ""), "实现 API UI 测试")

    assert [item.kind for item in design_items] == ["design_overview"]
    assert [item.kind for item in development_items] == ["api_implementation"]
    assert testing_items == []


def test_execution_scope_config_round_trip(tmp_path) -> None:
    path = tmp_path / "execution.config.json"
    config = ExecutionScopeConfig(
        requirement_design_enabled=True,
        design_detail_enabled=False,
        design_collaboration_enabled=False,
        backend_development_enabled=True,
        frontend_development_enabled=False,
        testing_enabled=True,
    )

    save_execution_scope_config(config, path)
    loaded = load_execution_scope_config(path)

    assert loaded.design_detail_enabled is False
    assert loaded.design_collaboration_enabled is False
    assert loaded.frontend_development_enabled is False
    assert loaded.testing_enabled is True


def test_resolve_run_profiles() -> None:
    mock = resolve_run_profile("mock")
    static_web = resolve_run_profile("static_web")
    fullstack_web = resolve_run_profile("fullstack_web")
    api_mock = resolve_run_profile("api_mock")
    api_sqlite = resolve_run_profile("api_sqlite")
    design = resolve_run_profile("design_cli_only")
    code = resolve_run_profile("code_cli")
    full = resolve_run_profile("full_cli")

    assert mock.cli_roles == []
    assert static_web.cli_roles == []
    assert static_web.enable_static_web_delivery is True
    assert static_web.require_real_code_outputs is False
    assert fullstack_web.cli_roles == []
    assert fullstack_web.enable_fullstack_web_delivery is True
    assert fullstack_web.require_real_code_outputs is False
    assert api_mock.cli_roles == []
    assert api_mock.enable_api_mock_delivery is True
    assert api_mock.require_real_code_outputs is False
    assert api_sqlite.cli_roles == []
    assert api_sqlite.enable_api_sqlite_delivery is True
    assert api_sqlite.require_real_code_outputs is False
    assert design.cli_roles == ["designer", "requirement_designer", "solution_designer"]
    assert design.require_real_design_outputs is True
    assert code.cli_roles == ["backend_engineer", "frontend_engineer"]
    assert code.require_real_code_outputs is True
    assert full.uses_cli_for_role("tester") is True
    assert full.require_real_design_outputs is True
    assert full.require_real_code_outputs is True
