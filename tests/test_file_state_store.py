"""FileStateStore tests."""

from conductor.controller.lead_controller import LeadController
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

    reloaded = FileStateStore(state_dir)
    restored = reloaded.get_state(state.project.id)

    assert restored.project.id == state.project.id
    assert restored.current_stage == state.current_stage
    assert len(restored.workitems) == len(state.workitems)
    assert restored.executions[0].agent_id == "agent-designer"
    assert restored.agent_capability_stats[0].completed_count == 1
    assert [activation.role for activation in restored.agent_activations] == [
        activation.role for activation in state.agent_activations
    ]
