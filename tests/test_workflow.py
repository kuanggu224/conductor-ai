"""Workflow 测试。"""

from conductor.domain.models import WorkItem, WorkItemStatus
from conductor.workflow.template import GateDecision, WorkflowGateEvaluator, WorkflowTemplate


def test_default_workflow_sequence() -> None:
    workflow = WorkflowTemplate()

    assert [stage.name for stage in workflow.stages] == ["requirement", "design", "development", "testing"]
    assert workflow.get_first_stage().name == "requirement"
    assert workflow.get_next_stage("requirement").name == "design"
    assert workflow.get_next_stage("design").name == "development"
    assert workflow.get_next_stage("testing") is None
    assert GateDecision.PASS.value == "pass"


def test_gate_evaluator_returns_expected_decisions() -> None:
    evaluator = WorkflowGateEvaluator()

    assert evaluator.evaluate("design", []) == GateDecision.PASS
    assert evaluator.evaluate(
        "design",
        [WorkItem(id="w1", description="设计", stage="design", status=WorkItemStatus.PENDING)],
    ) == GateDecision.REWORK
    assert evaluator.evaluate(
        "design",
        [WorkItem(id="w1", description="设计", stage="design", status=WorkItemStatus.DONE)],
    ) == GateDecision.PASS
    assert evaluator.evaluate(
        "design",
        [WorkItem(id="w1", description="设计", stage="design", status=WorkItemStatus.FAILED)],
    ) == GateDecision.RETRY
    assert evaluator.evaluate(
        "design",
        [WorkItem(id="w1", description="设计", stage="design", status=WorkItemStatus.FAILED, retry_count=1, max_retries=1)],
    ) == GateDecision.ESCALATE
