"""AgentTeamPlanner tests."""

from conductor.agents.registry import AgentRegistry
from conductor.agents.team_planner import AgentTeamPlanner
from conductor.controller.tl_agent import TechnicalLeadAgent
from conductor.domain.models import AgentCapabilityStats, Project, ProjectStatus, SharedProjectState, WorkItem, WorkItemStatus


def test_agent_team_planner_generates_same_role_backend_instances() -> None:
    planner = AgentTeamPlanner()
    state = SharedProjectState(
        project=Project(id="project-team", goal="Build a API page with form validation", current_stage="development"),
        project_status=ProjectStatus.IN_PROGRESS,
        current_stage="development",
        workitems=[
            WorkItem(
                id="workitem-api",
                description="Implement backend page, interactions, validation, and local state.",
                stage="development",
                kind="api_implementation",
            )
        ],
    )

    plan = planner.plan(state)

    backend_specs = [spec for spec in plan.agent_specs if spec.role == "backend_engineer"]
    assert plan.stage == "development"
    assert len(backend_specs) == 2
    assert {spec.instance_id for spec in backend_specs} == {"api_contracts", "data_model"}
    assert all(spec.parallel_safe for spec in backend_specs)
    assert all(spec.write_scope for spec in backend_specs)
    assert all(spec.collaboration_mode == "parallel_development" for spec in backend_specs)
    assert plan.parallel_protocol["enabled"] is True
    assert len(plan.parallel_protocol["lanes"]) == 2
    assert plan.parallel_protocol["merge_order"] == [spec.agent_id for spec in backend_specs]
    assert "Each lane must claim through Task Center before editing." in plan.parallel_protocol["shared_contracts"]
    assert plan.global_strategy["posture"] == "expand_parallel"
    assert plan.global_strategy["parallel_lane_count"] == 2


def test_agent_team_planner_generates_testing_peer_instances() -> None:
    planner = AgentTeamPlanner()
    state = SharedProjectState(
        project=Project(id="project-test-team", goal="Validate API errors and acceptance", current_stage="testing"),
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
        project=Project(id="project-req-team", goal="Build a API form with CSV export", current_stage="requirement"),
        project_status=ProjectStatus.IN_PROGRESS,
        current_stage="requirement",
        workitems=[WorkItem(id="workitem-req", description="API form with CSV export", stage="requirement", kind="requirement_spec")],
    )
    design_state = SharedProjectState(
        project=Project(id="project-design-team", goal="Build a API form with CSV export", current_stage="design"),
        project_status=ProjectStatus.IN_PROGRESS,
        current_stage="design",
        workitems=[WorkItem(id="workitem-design", description="API form with CSV export", stage="design", kind="design_overview")],
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
    base_profile = registry.get_profile_by_role("backend_engineer")
    state = SharedProjectState(
        project=Project(id="project-registry", goal="Build backend API", current_stage="development"),
        project_status=ProjectStatus.IN_PROGRESS,
        current_stage="development",
        workitems=[WorkItem(id="workitem-api", description="API", stage="development", kind="api_implementation")],
    )
    spec = next(spec for spec in planner.plan(state).agent_specs if spec.instance_id == "api_contracts")
    profile = planner.profile_for_spec(base_profile, spec)

    agent = registry.register_dynamic_agent(profile, agent_id=spec.agent_id, base_role=spec.role)

    assert agent.id == "agent-backend-engineer-api-contracts"
    assert agent.role == "backend_engineer"
    assert registry.get_agent_by_id(agent.id) is agent
    assert agent.profile is profile


def test_tl_agent_owns_dynamic_team_plan_decision() -> None:
    planner = AgentTeamPlanner()
    tl_agent = TechnicalLeadAgent()
    state = SharedProjectState(
        project=Project(id="project-tl-team", goal="Build an API and API page with data storage.", current_stage="development"),
        project_status=ProjectStatus.IN_PROGRESS,
        current_stage="development",
        workitems=[
            WorkItem(id="workitem-api", description="Implement API page.", stage="development", kind="api_implementation"),
            WorkItem(id="workitem-api", description="Implement API and data model.", stage="development", kind="api_implementation"),
        ],
    )

    plan = tl_agent.plan_agent_team(state, planner)

    assert plan.decision_source == "tl_agent"
    assert plan.decided_by == "tl_agent"
    assert "candidate_specs=" in plan.decision_summary
    assert "posture=coordinate_parallel_delivery" in plan.decision_summary
    assert plan.global_strategy["posture"] == "coordinate_parallel_delivery"
    assert plan.global_strategy["recommended_next_action"] == "claim_lanes_then_integrate"
    assert plan.parallel_protocol["enabled"] is True
    assert plan.parallel_protocol["integration_owner"]
    assert plan.parallel_protocol["guard_lanes"] == []
    assert any(spec.role == "backend_engineer" for spec in plan.agent_specs)
    assert any(spec.role == "backend_engineer" for spec in plan.agent_specs)


def test_tl_agent_marks_escalation_as_human_action_required() -> None:
    tl_agent = TechnicalLeadAgent()
    state = SharedProjectState(
        project=Project(id="project-tl-escalate", goal="Build risky feature", current_stage="testing"),
        project_status=ProjectStatus.IN_PROGRESS,
        current_stage="testing",
    )

    decision = tl_agent.evaluate(state, "escalate_project")

    assert decision.action == "escalate_project"
    assert decision.human_action_required is True
    assert decision.strategy["posture"] == "hold"
    assert decision.strategy["recommended_next_action"] == "wait_for_human_control"


def test_tl_agent_adds_integration_contract_guard_for_parallel_backend_backend_work() -> None:
    planner = AgentTeamPlanner()
    tl_agent = TechnicalLeadAgent()
    state = SharedProjectState(
        project=Project(
            id="project-tl-integration",
            goal="Build a API, API, data storage, validation, and shared contract.",
            current_stage="development",
        ),
        project_status=ProjectStatus.IN_PROGRESS,
        current_stage="development",
        workitems=[
            WorkItem(
                id="workitem-api",
                description="Implement backend API with validation and API data flow.",
                stage="development",
                kind="api_implementation",
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
    assert "integration_risk=0" in plan.decision_summary
    assert plan.global_strategy["risk_drivers"] == []
    assert all(spec.instance_id != "integration_contract_guard" for spec in plan.agent_specs)
    assert plan.parallel_protocol["integration_owner"]
    assert "Backend/backend changes must pass integration contract review before final validation." not in plan.parallel_protocol["shared_contracts"]


def test_tl_agent_adds_feature_slice_delivery_guard() -> None:
    planner = AgentTeamPlanner()
    tl_agent = TechnicalLeadAgent()
    state = SharedProjectState(
        project=Project(id="project-tl-feature-slice", goal="Build multi-feature todo app.", current_stage="development"),
        project_status=ProjectStatus.IN_PROGRESS,
        current_stage="development",
        workitems=[
            WorkItem(
                id="workitem-api",
                description="Implement API according to feature slices.",
                stage="development",
                kind="api_implementation",
                acceptance_criteria=[
                    "Implement feature slices in milestone order: create_item -> list_items -> filter_items -> delete_item -> stats"
                ],
            )
        ],
    )

    plan = tl_agent.plan_agent_team(state, planner, trigger="stage_start")

    assert plan.decision_source == "tl_agent"
    assert "feature_slice_items=1" in plan.decision_summary
    assert plan.complexity_level in {"standard", "complex"}
    guard = next(spec for spec in plan.agent_specs if spec.instance_id == "feature_slice_delivery_guard")
    assert guard.role == "solution_designer"
    assert guard.collaboration_mode == "sequential_review"
    assert guard.workitem_kinds == ["api_implementation"]
    assert "feature slice order" in guard.scope
    assert any("milestone feature-slice constraints in development" in reason for reason in plan.reasons)


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
        project=Project(id="project-tl-history", goal="Build API and API implementation.", current_stage="development"),
        project_status=ProjectStatus.IN_PROGRESS,
        current_stage="development",
        workitems=[
            WorkItem(
                id="workitem-api",
                description="Implement API page.",
                stage="development",
                kind="api_implementation",
            )
        ],
        agent_capability_stats=[
            AgentCapabilityStats(
                agent_id="agent-backend",
                role="backend_engineer",
                completed_count=1,
                failed_count=3,
                workitem_kinds=["api_implementation"],
                last_workitem_id="workitem-prior",
                last_status="failed",
            )
        ],
    )

    plan = tl_agent.plan_agent_team(state, planner)

    assert plan.decision_source == "tl_agent"
    assert "history_risks=1" in plan.decision_summary
    assert plan.complexity_level == "standard"
    assert any(spec.role == "tester" and spec.instance_id == "history_quality_review" for spec in plan.agent_specs)
    assert any("weak historical performance for backend_engineer" in reason for reason in plan.reasons)


def test_tl_agent_adds_rework_acceptance_guard_for_missing_checklist_evidence() -> None:
    planner = AgentTeamPlanner()
    tl_agent = TechnicalLeadAgent()
    state = SharedProjectState(
        project=Project(id="project-tl-rework", goal="Fix reading list API rework.", current_stage="development"),
        project_status=ProjectStatus.IN_PROGRESS,
        current_stage="development",
        workitems=[
            WorkItem(
                id="workitem-rework",
                description="Fix API rework from failed validation.",
                stage="development",
                kind="api_implementation",
                feedback_from=["workitem-api-test"],
                rework_of="workitem-api",
                acceptance_criteria=[
                    "修复测试反馈 workitem-api-test",
                    (
                        "Address missing testing checklist `add_item` add item interaction: "
                        "produce evidence api client form interaction updated visible state"
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
    assert guard.workitem_kinds == ["api_implementation"]
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


def test_tl_agent_adds_coordination_guard_for_broad_development_scope() -> None:
    planner = AgentTeamPlanner()
    tl_agent = TechnicalLeadAgent()
    state = SharedProjectState(
        project=Project(id="project-tl-coordination", goal="Implement a broad project execution workflow.", current_stage="development"),
        project_status=ProjectStatus.IN_PROGRESS,
        current_stage="development",
        workitems=[
            WorkItem(
                id="workitem-parser",
                description="Implement requirement intake parser.",
                stage="development",
                kind="generic_implementation",
                acceptance_criteria=["Parser handles structured intake.", "Parser records validation errors."],
            ),
            WorkItem(
                id="workitem-scheduler",
                description="Implement execution scheduling rules.",
                stage="development",
                kind="generic_implementation",
                acceptance_criteria=["Scheduler respects dependencies.", "Scheduler records blocked tasks."],
            ),
            WorkItem(
                id="workitem-reporter",
                description="Implement delivery reporting outputs.",
                stage="development",
                kind="generic_implementation",
                acceptance_criteria=["Reporter writes audit summary.", "Reporter links generated artifacts."],
            ),
        ],
    )

    plan = tl_agent.plan_agent_team(state, planner, trigger="stage_start")

    assert plan.decision_source == "tl_agent"
    assert "coordination_risk=1" in plan.decision_summary
    assert plan.complexity_level == "standard"
    guard = next(spec for spec in plan.agent_specs if spec.instance_id == "implementation_coordination_guard")
    assert guard.role == "solution_designer"
    assert guard.collaboration_mode == "sequential_review"
    assert guard.workitem_kinds == ["generic_implementation"]
    assert "write scopes" in guard.scope
    assert any("broad development scope" in reason for reason in plan.reasons)
