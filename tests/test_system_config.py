"""SystemConfig tests."""

from conductor.config.system import AgentsConfig, CollaborationConfig, PlannerConfig, RoleMappingConfig, SystemConfig, WorkflowConfig


def test_system_config_save_and_load_round_trip(tmp_path) -> None:
    config = SystemConfig(
        workflow=WorkflowConfig(
            stages=[
                {
                    "name": "design",
                    "objective": "明确需求并形成方案",
                    "expected_output": "设计说明",
                }
            ]
        ),
        role_mapping=RoleMappingConfig(
            kind_to_role={"design_overview": "designer"},
            default_role="backend_engineer",
        ),
        planner=PlannerConfig(
            ui_keywords=("ui", "页面"),
            api_keywords=("api", "接口"),
            test_keywords=("测试", "pytest"),
            data_keywords=("数据", "schema"),
            design_workitem_kinds=["design_overview"],
            development_workitem_kinds=["api_implementation"],
            testing_workitem_kinds=["acceptance_check"],
        ),
        collaboration=CollaborationConfig(
            enabled=True,
            max_rounds=3,
            lead_role_by_stage={"design": "designer"},
            reviewer_roles_by_stage={"design": ["backend_engineer", "tester"]},
            enabled_kinds={"design_overview"},
        ),
        agents=AgentsConfig(
            default_profiles=[
                {
                    "role_name": "designer",
                    "mission": "把需求转成设计输出",
                    "default_cli_name": "codex",
                }
            ]
        ),
    )

    path = config.save(tmp_path / "system.config.json")
    loaded = SystemConfig.load(path)

    assert path.exists()
    assert loaded.workflow.stages[0]["name"] == "design"
    assert loaded.role_mapping.get_role_for_kind("unknown") == "backend_engineer"
    assert loaded.role_mapping.workitem_kind_to_role["design_overview"] == "designer"
    assert loaded.planner.ui_keywords == ("ui", "页面")
    assert loaded.collaboration.max_rounds == 3
    assert loaded.collaboration.enabled_kinds == {"design_overview"}
    assert loaded.agents.default_profiles[0]["role_name"] == "designer"
