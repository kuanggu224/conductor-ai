"""AgentTeamPlanner tests."""

from conductor.agents.registry import AgentRegistry
from conductor.agents.team_planner import AgentTeamPlanner
from conductor.controller.tl_agent import TechnicalLeadAgent
from conductor.domain.models import Project, ProjectStatus, SharedProjectState, WorkItem, WorkItemStatus


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


def test_tl_agent_owns_dynamic_team_plan_decision() -> None:
    planner = AgentTeamPlanner()
    tl_agent = TechnicalLeadAgent()
    state = SharedProjectState(
        project=Project(id="project-tl-team", goal="Build an API and UI page with data storage.", current_stage="development"),
        project_status=ProjectStatus.IN_PROGRESS,
        current_stage="development",
        workitems=[
            WorkItem(id="workitem-ui", description="Implement UI page.", stage="development", kind="ui_implementation"),
            WorkItem(id="workitem-api", description="Implement API and data model.", stage="development", kind="api_implementation"),
        ],
    )

    plan = tl_agent.plan_agent_team(state, planner)

    assert plan.decision_source == "tl_agent"
    assert plan.decided_by == "tl_agent"
    assert "candidate_specs=" in plan.decision_summary
    assert any(spec.role == "frontend_engineer" for spec in plan.agent_specs)
    assert any(spec.role == "backend_engineer" for spec in plan.agent_specs)


def test_tl_agent_adds_runtime_failure_triage_agent() -> None:
    planner = AgentTeamPlanner()
    tl_agent = TechnicalLeadAgent()
    state = SharedProjectState(
        project=Project(id="project-tl-risk", goal="Build a backend worker.", current_stage="development"),
        project_status=ProjectStatus.IN_PROGRESS,
        current_stage="development",
        workitems=[
            WorkItem(
                id="workitem-dev",
                description="Implement backend worker.",
                stage="development",
                kind="generic_implementation",
                status=WorkItemStatus.FAILED,
                retry_count=1,
            )
        ],
    )

    plan = tl_agent.plan_agent_team(state, planner, trigger="runtime_risk")

    assert plan.decision_source == "tl_agent"
    assert plan.complexity_level == "complex"
    assert any(spec.role == "tester" and spec.instance_id == "failure_triage" for spec in plan.agent_specs)
