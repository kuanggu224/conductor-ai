"""Harness 与 Gate 联动测试。"""

from dataclasses import replace

from conductor.controller.lead_controller import LeadController
from conductor.domain.models import ExecutionStatus, ProjectStatus, WorkItem
from conductor.harness.base import BaseHarness
from conductor.harness.models import HarnessRequest, HarnessResult
from conductor.execution.runner import Runner
from conductor.state.store import InMemoryStateStore
from conductor.workflow.template import WorkflowTemplate


class FailOnceHarness(BaseHarness):
    name = "shell"

    def __init__(self) -> None:
        self.called = 0

    def run(self, request: HarnessRequest) -> HarnessResult:
        self.called += 1
        if self.called == 1:
            return HarnessResult(
                success=False,
                exit_code=1,
                stdout="1 failed",
                stderr="traceback",
                duration_ms=10,
            )
        return HarnessResult(
            success=True,
            exit_code=0,
            stdout="3 passed",
            stderr="",
            duration_ms=8,
        )


def test_harness_failure_enters_retry_then_recovers() -> None:
    state_store = InMemoryStateStore()
    runner = Runner(
        state_store,
        shell_harness=FailOnceHarness(),
        enable_tester_harness=True,
    )
    controller = LeadController(
        workflow_template=WorkflowTemplate(),
        state_store=state_store,
        runner=runner,
    )
    state = controller.initialize_project("实现 API 和测试")
    automated_test = WorkItem(
        id="workitem-test",
        description="执行自动化测试",
        stage="testing",
        kind="automated_test",
    )
    state = replace(
        state,
        workitems=[automated_test],
        current_stage="testing",
        project=replace(state.project, current_stage="testing", status=ProjectStatus.IN_PROGRESS),
        project_status=ProjectStatus.IN_PROGRESS,
    )
    state_store.save_state(state)

    state = controller.advance(state)
    assert state.executions[-1].status == ExecutionStatus.FAILED
    assert state.workitems[0].status.value == "failed"

    state = controller.advance(state)
    assert state.workitems[0].status.value == "pending"
    assert state.gate_history[-1] == "testing:retry"

    state = controller.advance(state)
    assert state.executions[-1].status == ExecutionStatus.SUCCESS
    assert state.workitems[0].status.value == "done"
