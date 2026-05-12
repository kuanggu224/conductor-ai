"""Human control-plane tests."""

from conductor.control.human import HumanControlService
from conductor.domain.models import HumanControlActionType, Project, ProjectStatus, SharedProjectState
from conductor.state.store import InMemoryStateStore


def test_human_control_pause_resume_and_approval_hold() -> None:
    store = InMemoryStateStore()
    state = SharedProjectState(
        project=Project(id="project-human", goal="Build a local tool"),
        project_status=ProjectStatus.IN_PROGRESS,
        current_stage="development",
    )
    store.save_state(state)
    service = HumanControlService(store)

    paused = service.pause("project-human", actor="operator", reason="inspect output")

    assert paused.human_control_actions[-1].action == HumanControlActionType.PAUSE
    assert service.controller_hold_reason(paused) == "human_paused: inspect output"

    resumed = service.resume("project-human", actor="operator", reason="continue")

    assert service.controller_hold_reason(resumed) is None

    approval = service.request_approval("project-human", reason="high risk stage")

    assert approval.human_control_actions[-1].action == HumanControlActionType.REQUEST_APPROVAL
    assert service.controller_hold_reason(approval) == "human_approval_required: high risk stage"

    approved = service.approve("project-human", actor="operator", reason="accepted")

    assert service.controller_hold_reason(approved) is None

