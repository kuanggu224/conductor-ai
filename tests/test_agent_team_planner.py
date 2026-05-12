"""AgentTeamPlanner tests."""

from conductor.agents.registry import AgentRegistry
from conductor.agents.team_planner import AgentTeamPlanner
from conductor.domain.models import Project, ProjectStatus, SharedProjectState, WorkItem


def test_agent_team_planner_generates_same_role_frontend_instances() -> None:
    planner = AgentTeamPlanner()
    state = SharedProjectState(
        project=Project(id="project-team", goal="Build a UI page with form validation", current_stage="development"),
        project_status=ProjectStatus.IN_PROGRESS,
        current_stage="development",
        workitems=[
            WorkItem(
                id="workitem-ui",
                description="Implement frontend page, interactions, validation, and local state.",
                stage="development",
                kind="ui_implementation",
            )
        ],
    )

    plan = planner.plan(state)

    frontend_specs = [spec for spec in plan.agent_specs if spec.role == "frontend_engineer"]
    assert plan.stage == "development"
    assert len(frontend_specs) == 2
    assert {spec.instance_id for spec in frontend_specs} == {"ui_layout", "state_logic"}
    assert all(spec.parallel_safe for spec in frontend_specs)
    assert all(spec.write_scope for spec in frontend_specs)
    assert all(spec.collaboration_mode == "parallel_development" for spec in frontend_specs)


def test_agent_team_planner_generates_testing_peer_instances() -> None:
    planner = AgentTeamPlanner()
    state = SharedProjectState(
        project=Project(id="project-test-team", goal="Validate UI errors and acceptance", current_stage="testing"),
        project_status=ProjectStatus.IN_PROGRESS,
        current_stage="testing",
        workitems=[
            WorkItem(
                id="workitem-acceptance",
                description="Validate acceptance and edge cases.",
                stage="testing",
                kind="acceptance_check",
            )
        ],
    )

    plan = planner.plan(state)

    tester_specs = [spec for spec in plan.agent_specs if spec.role == "tester"]
    assert {spec.instance_id for spec in tester_specs} == {"acceptance", "edge_cases"}
    assert all(spec.collaboration_mode == "parallel_review" for spec in tester_specs)


def test_agent_team_planner_keeps_simple_acceptance_check_linear() -> None:
    planner = AgentTeamPlanner()
    state = SharedProjectState(
        project=Project(id="project-simple-test", goal="实现最小骨架", current_stage="testing"),
        project_status=ProjectStatus.IN_PROGRESS,
        current_stage="testing",
        workitems=[
            WorkItem(
                id="workitem-acceptance",
                description="验收整体交付物",
                stage="testing",
                kind="acceptance_check",
            )
        ],
    )

    plan = planner.plan(state)

    assert plan.agent_specs == []


def test_agent_registry_registers_dynamic_agent_instance() -> None:
    registry = AgentRegistry()
    planner = AgentTeamPlanner()
    base_profile = registry.get_profile_by_role("frontend_engineer")
    state = SharedProjectState(
        project=Project(id="project-registry", goal="Build frontend UI", current_stage="development"),
        project_status=ProjectStatus.IN_PROGRESS,
        current_stage="development",
        workitems=[WorkItem(id="workitem-ui", description="UI", stage="development", kind="ui_implementation")],
    )
    spec = next(spec for spec in planner.plan(state).agent_specs if spec.instance_id == "ui_layout")
    profile = planner.profile_for_spec(base_profile, spec)

    agent = registry.register_dynamic_agent(profile, agent_id=spec.agent_id, base_role=spec.role)

    assert agent.id == "agent-frontend-engineer-ui-layout"
    assert agent.role == "frontend_engineer"
    assert registry.get_agent_by_id(agent.id) is agent
    assert agent.profile is profile
