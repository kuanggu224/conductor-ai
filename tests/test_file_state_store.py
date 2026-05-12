"""FileStateStore tests."""

from dataclasses import replace

from conductor.agents.team_planner import AgentTeamPlanner
from conductor.controller.lead_controller import LeadController
from conductor.domain.models import Project, ProjectStatus, SharedProjectState, WorkItem
from conductor.execution.runner import Runner
from conductor.state.file_store import FileStateStore
from conductor.workflow.template import WorkflowTemplate


def test_file_state_store_persists_and_reloads_project(tmp_path) -> None:
    state_dir = tmp_path / "state"
    store = FileStateStore(state_dir)
    runner = Runner(state_store=store)
    controller = LeadController(
        workflow_template=WorkflowTemplate(),
        state_store=store,
        runner=runner,
    )
    state = controller.initialize_project("实现一个 API 和 UI 页面")
    state = controller.advance(state)
    state = controller.human_control.pause(state.project.id, actor="operator", reason="checkpoint")
    state = replace(
        state,
        workitems=[
            replace(
                state.workitems[0],
                testing_checklist=[
                    {
                        "rule_id": "add_item",
                        "label": "add item interaction",
                        "status": "pending",
                    }
                ],
            ),
            *state.workitems[1:],
        ],
    )
    store.save_state(state)

    reloaded = FileStateStore(state_dir)
    restored = reloaded.get_state(state.project.id)

    assert restored.project.id == state.project.id
    assert restored.current_stage == state.current_stage
    assert len(restored.workitems) == len(state.workitems)
    assert restored.executions[0].agent_id == "agent-requirement-designer"
    assert isinstance(restored.executions[0].input_artifact_ids, list)
    assert isinstance(restored.executions[0].execution_command, list)
    assert isinstance(restored.executions[0].prompt_hash, str)
    assert isinstance(restored.executions[0].token_usage, dict)
    assert isinstance(restored.task_assignments[0].claim_token, str)
    assert isinstance(restored.task_assignments[0].last_heartbeat_at, str)
    assert restored.workitems[0].testing_checklist[0]["rule_id"] == "add_item"
    assert restored.agent_capability_stats[0].completed_count == 1
    assert restored.tl_decisions[0].action == "execute_workitem"
    assert restored.human_control_actions[0].action.value == "pause"
    assert restored.human_control_actions[0].reason == "checkpoint"
    assert [activation.role for activation in restored.agent_activations] == [
        activation.role for activation in state.agent_activations
    ]


def test_file_state_store_persists_execution_token_usage(tmp_path) -> None:
    state_dir = tmp_path / "state"
    store = FileStateStore(state_dir)
    runner = Runner(state_store=store)
    controller = LeadController(
        workflow_template=WorkflowTemplate(),
        state_store=store,
        runner=runner,
    )
    state = controller.initialize_project("Build a reading list")
    state = controller.advance(state)
    state = replace(
        state,
        executions=[
            replace(
                state.executions[0],
                token_usage={"prompt_tokens": 12, "completion_tokens": 8, "total_tokens": 20},
            )
        ],
    )
    store.save_state(state)

    restored = FileStateStore(state_dir).get_state(state.project.id)

    assert restored.executions[0].token_usage == {
        "prompt_tokens": 12,
        "completion_tokens": 8,
        "total_tokens": 20,
    }


def test_file_state_store_persists_agent_team_plans(tmp_path) -> None:
    state_dir = tmp_path / "state"
    store = FileStateStore(state_dir)
    state = SharedProjectState(
        project=Project(id="project-team-plan", goal="Build UI with validation", current_stage="development"),
        project_status=ProjectStatus.IN_PROGRESS,
        current_stage="development",
        workitems=[
            WorkItem(
                id="workitem-ui",
                description="Implement UI validation",
                stage="development",
                kind="ui_implementation",
            )
        ],
    )
    plan = AgentTeamPlanner().plan(state)
    store.save_state(replace(state, agent_team_plans=[plan]))

    restored = FileStateStore(state_dir).get_state("project-team-plan")

    assert restored.agent_team_plans[0].stage == "development"
    assert restored.agent_team_plans[0].agent_specs[0].agent_id.startswith("agent-")
    assert restored.agent_team_plans[0].agent_specs[0].write_scope


def test_file_state_store_quarantines_corrupt_state_files(tmp_path) -> None:
    state_dir = tmp_path / "state"
    state_dir.mkdir()
    corrupt_path = state_dir / "project-bad.state.json"
    corrupt_path.write_text("{not-json", encoding="utf-8")

    store = FileStateStore(state_dir)

    assert store.list_states() == []
    assert not corrupt_path.exists()
    quarantined = list(state_dir.glob("project-bad.state.json.corrupt-*"))
    assert len(quarantined) == 1
    assert store.corrupt_state_files == [str(quarantined[0])]
