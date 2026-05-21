"""AgentTeamPlanner tests."""

from conductor.agents.registry import AgentRegistry
from conductor.agents.team_planner import AgentTeamPlanner
from conductor.controller.tl_agent import TechnicalLeadAgent
from conductor.domain.models import AgentCapabilityStats, Project, ProjectStatus, SharedProjectState, WorkItem, WorkItemStatus


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


def test_agent_team_planner_stage_scopes_planning_agent_ids() -> None:
    planner = AgentTeamPlanner()
    requirement_state = SharedProjectState(
        project=Project(id="project-req-team", goal="Build a UI form with CSV export", current_stage="requirement"),
        project_status=ProjectStatus.IN_PROGRESS,
        current_stage="requirement",
        workitems=[WorkItem(id="workitem-req", description="UI form with CSV export", stage="requirement", kind="requirement_spec")],
    )
    design_state = SharedProjectState(
        project=Project(id="project-design-team", goal="Build a UI form with CSV export", current_stage="design"),
        project_status=ProjectStatus.IN_PROGRESS,
        current_stage="design",
        workitems=[WorkItem(id="workitem-design", description="UI form with CSV export", stage="design", kind="design_overview")],
    )

    requirement_ids = {spec.agent_id for spec in planner.plan(requirement_state).agent_specs}
    design_ids = {spec.agent_id for spec in planner.plan(design_state).agent_specs}

    assert requirement_ids
    assert design_ids
    assert requirement_ids.isdisjoint(design_ids)
    assert all("-requirement-" in agent_id for agent_id in requirement_ids)
    assert all("-design-" in agent_id for agent_id in design_ids)


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


def test_tl_agent_adds_integration_contract_guard_for_parallel_frontend_backend_work() -> None:
    planner = AgentTeamPlanner()
    tl_agent = TechnicalLeadAgent()
    state = SharedProjectState(
        project=Project(
            id="project-tl-integration",
            goal="Build a UI, API, data storage, validation, and shared contract.",
            current_stage="development",
        ),
        project_status=ProjectStatus.IN_PROGRESS,
        current_stage="development",
        workitems=[
            WorkItem(
                id="workitem-ui",
                description="Implement frontend UI with validation and API data flow.",
                stage="development",
                kind="ui_implementation",
            ),
            WorkItem(
                id="workitem-api",
                description="Implement backend API, schema, storage, and validation contract.",
                stage="development",
                kind="api_implementation",
            ),
        ],
    )

    plan = tl_agent.plan_agent_team(state, planner)

    assert plan.decision_source == "tl_agent"
    assert "integration_risk=1" in plan.decision_summary
    guard = next(spec for spec in plan.agent_specs if spec.instance_id == "integration_contract_guard")
    assert guard.role == "solution_designer"
    assert guard.collaboration_mode == "sequential_review"
    assert guard.workitem_kinds == ["ui_implementation", "api_implementation"]
    assert "API contracts" in guard.scope
    assert any("integration contract guard" in reason for reason in plan.reasons)


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


def test_tl_agent_uses_agent_history_to_add_quality_review() -> None:
    planner = AgentTeamPlanner()
    tl_agent = TechnicalLeadAgent()
    state = SharedProjectState(
        project=Project(id="project-tl-history", goal="Build UI and API implementation.", current_stage="development"),
        project_status=ProjectStatus.IN_PROGRESS,
        current_stage="development",
        workitems=[
            WorkItem(
                id="workitem-ui",
                description="Implement UI page.",
                stage="development",
                kind="ui_implementation",
            )
        ],
        agent_capability_stats=[
            AgentCapabilityStats(
                agent_id="agent-frontend",
                role="frontend_engineer",
                completed_count=1,
                failed_count=3,
                workitem_kinds=["ui_implementation"],
                last_workitem_id="workitem-prior",
                last_status="failed",
            )
        ],
    )

    plan = tl_agent.plan_agent_team(state, planner)

    assert plan.decision_source == "tl_agent"
    assert "history_risks=1" in plan.decision_summary
    assert plan.complexity_level == "complex"
    assert any(spec.role == "tester" and spec.instance_id == "history_quality_review" for spec in plan.agent_specs)
    assert any("weak historical performance for frontend_engineer" in reason for reason in plan.reasons)


def test_tl_agent_adds_rework_acceptance_guard_for_missing_checklist_evidence() -> None:
    planner = AgentTeamPlanner()
    tl_agent = TechnicalLeadAgent()
    state = SharedProjectState(
        project=Project(id="project-tl-rework", goal="Fix reading list UI rework.", current_stage="development"),
        project_status=ProjectStatus.IN_PROGRESS,
        current_stage="development",
        workitems=[
            WorkItem(
                id="workitem-rework",
                description="Fix UI rework from failed validation.",
                stage="development",
                kind="ui_implementation",
                feedback_from=["workitem-ui-test"],
                rework_of="workitem-ui",
                acceptance_criteria=[
                    "修复测试反馈 workitem-ui-test",
                    (
                        "Address missing testing checklist `add_item` add item interaction: "
                        "produce evidence browser form interaction updated visible state"
                    ),
                ],
            )
        ],
    )

    plan = tl_agent.plan_agent_team(state, planner, trigger="feedback_rework")

    assert plan.decision_source == "tl_agent"
    assert "rework_evidence_items=1" in plan.decision_summary
    assert plan.complexity_level == "standard"
    guard = next(spec for spec in plan.agent_specs if spec.instance_id == "rework_acceptance_guard")
    assert guard.role == "tester"
    assert guard.collaboration_mode == "sequential_review"
    assert guard.workitem_kinds == ["ui_implementation"]
    assert any("missing checklist evidence targets: workitem-rework" in reason for reason in plan.reasons)


def test_tl_agent_adds_testing_evidence_trace_guard_for_checklist_contracts() -> None:
    planner = AgentTeamPlanner()
    tl_agent = TechnicalLeadAgent()
    state = SharedProjectState(
        project=Project(id="project-tl-test-evidence", goal="Validate API behavior.", current_stage="testing"),
        project_status=ProjectStatus.IN_PROGRESS,
        current_stage="testing",
        workitems=[
            WorkItem(
                id="workitem-api-validation",
                description="Validate endpoint behavior and evidence trace.",
                stage="testing",
                kind="api_validation",
                testing_checklist=[
                    {
                        "rule_id": "api_behavior",
                        "label": "API endpoint behavior",
                        "status": "pending",
                        "required_evidence_terms": ["endpoint path", "HTTP status code", "response payload"],
                    }
                ],
            )
        ],
    )

    plan = tl_agent.plan_agent_team(state, planner, trigger="stage_start")

    assert plan.decision_source == "tl_agent"
    assert "testing_checklist_items=1" in plan.decision_summary
    assert plan.complexity_level == "standard"
    guard = next(spec for spec in plan.agent_specs if spec.instance_id == "evidence_trace_guard")
    assert guard.role == "tester"
    assert guard.collaboration_mode == "sequential_review"
    assert guard.workitem_kinds == ["api_validation"]
    assert "required evidence terms" in guard.scope
    assert any("testing checklist evidence contracts requiring trace audit" in reason for reason in plan.reasons)
