"""Delivery readiness assessment for stable project handoff."""

from __future__ import annotations

from dataclasses import dataclass, field

from conductor.artifacts.scope_contract import evaluate_scope_contract
from conductor.artifacts.store import ArtifactStore
from conductor.domain.models import Artifact, ProjectStatus, SharedProjectState


@dataclass(slots=True)
class DeliveryReadinessCheck:
    """One auditable readiness check."""

    id: str
    label: str
    status: str
    severity: str
    evidence: str


@dataclass(slots=True)
class DeliveryReadinessResult:
    """Project-level readiness summary."""

    status: str
    score: int
    checks: list[DeliveryReadinessCheck] = field(default_factory=list)

    @property
    def blocking_count(self) -> int:
        return len([check for check in self.checks if check.status == "fail" and check.severity == "blocker"])

    @property
    def warning_count(self) -> int:
        return len([check for check in self.checks if check.status == "warn"])

    def to_dict(self) -> dict[str, object]:
        """Return a manifest-safe payload."""
        return {
            "status": self.status,
            "score": self.score,
            "blocking_count": self.blocking_count,
            "warning_count": self.warning_count,
            "checks": [
                {
                    "id": check.id,
                    "label": check.label,
                    "status": check.status,
                    "severity": check.severity,
                    "evidence": check.evidence,
                }
                for check in self.checks
            ],
        }


CODE_WORKITEM_KINDS = {"api_implementation", "data_implementation", "generic_implementation", "ui_implementation"}
DESIGN_ARTIFACT_KINDS = {"design_overview", "ui_design", "api_design", "test_design"}
TEST_WORKITEM_KINDS = {"acceptance_check", "automated_test", "api_validation", "ui_validation"}
SKIP_SCOPE_KINDS = {"requirement_spec", "frozen_requirement_spec", "collaboration_review"}


def evaluate_delivery_readiness(
    state: SharedProjectState,
    *,
    artifact_store: ArtifactStore | None = None,
) -> DeliveryReadinessResult:
    """Assess whether the current project has enough evidence for stable handoff."""
    store = artifact_store or ArtifactStore()
    checks = [
        _frozen_requirement_check(state),
        _stage_completion_check(state),
        _failure_blocker_check(state),
        _design_artifact_check(state),
        _code_evidence_check(state),
        _validation_evidence_check(state),
        _scope_contract_check(state, store),
    ]
    score = _score(checks)
    status = _status(checks, score)
    return DeliveryReadinessResult(status=status, score=score, checks=checks)


def render_delivery_readiness_markdown(result: DeliveryReadinessResult) -> list[str]:
    """Render readiness checks for project reports."""
    lines = [
        f"- Status: {result.status}",
        f"- Score: {result.score}",
        f"- Blocking Checks: {result.blocking_count}",
        f"- Warning Checks: {result.warning_count}",
    ]
    for check in result.checks:
        lines.append(
            f"- [{check.status}] {check.id} | severity={check.severity} | {check.label} | evidence={check.evidence}"
        )
    return lines


def _frozen_requirement_check(state: SharedProjectState) -> DeliveryReadinessCheck:
    artifact = _latest_artifact(state, {"frozen_requirement_spec"})
    return DeliveryReadinessCheck(
        id="frozen_requirement",
        label="Frozen requirement exists",
        status="pass" if artifact else "fail",
        severity="blocker",
        evidence=artifact.id if artifact else "missing frozen_requirement_spec artifact",
    )


def _stage_completion_check(state: SharedProjectState) -> DeliveryReadinessCheck:
    pending = [item.id for item in state.workitems if item.status.value in {"pending", "running"}]
    if state.project_status == ProjectStatus.COMPLETED and not pending:
        status = "pass"
        evidence = "project completed and no pending/running WorkItems"
    elif pending:
        status = "warn"
        evidence = "pending/running WorkItems: " + ", ".join(pending)
    else:
        status = "warn"
        evidence = f"project status is {state.project_status.value}"
    return DeliveryReadinessCheck(
        id="stage_completion",
        label="Workflow reached a handoff-ready state",
        status=status,
        severity="warning",
        evidence=evidence,
    )


def _failure_blocker_check(state: SharedProjectState) -> DeliveryReadinessCheck:
    failed = [item.id for item in state.workitems if item.status.value == "failed"]
    if failed or state.blockers:
        evidence_parts = []
        if failed:
            evidence_parts.append("failed WorkItems: " + ", ".join(failed))
        if state.blockers:
            evidence_parts.append("blockers: " + "; ".join(state.blockers))
        return DeliveryReadinessCheck(
            id="failure_blockers",
            label="No unresolved failures or blockers",
            status="fail",
            severity="blocker",
            evidence=" | ".join(evidence_parts),
        )
    return DeliveryReadinessCheck(
        id="failure_blockers",
        label="No unresolved failures or blockers",
        status="pass",
        severity="blocker",
        evidence="none",
    )


def _design_artifact_check(state: SharedProjectState) -> DeliveryReadinessCheck:
    artifacts = [artifact.id for artifact in state.artifacts if artifact.kind in DESIGN_ARTIFACT_KINDS]
    return DeliveryReadinessCheck(
        id="design_artifacts",
        label="Design handoff artifact exists",
        status="pass" if artifacts else "warn",
        severity="warning",
        evidence=", ".join(artifacts) if artifacts else "no design artifact recorded",
    )


def _code_evidence_check(state: SharedProjectState) -> DeliveryReadinessCheck:
    code_executions = [execution for execution in state.executions if _workitem_kind(state, execution.workitem_id) in CODE_WORKITEM_KINDS]
    changed_files = sorted({path for execution in code_executions for path in execution.changed_files})
    code_artifacts = [
        artifact.id
        for artifact in state.artifacts
        if artifact.kind in CODE_WORKITEM_KINDS and not artifact.source_backend.startswith(("mock", "real_backend_required"))
    ]
    if changed_files:
        status = "pass"
        evidence = "changed files: " + ", ".join(changed_files[:8])
    elif code_artifacts:
        status = "warn"
        evidence = "code artifacts without changed-file evidence: " + ", ".join(code_artifacts)
    else:
        status = "warn"
        evidence = "no concrete code change evidence"
    return DeliveryReadinessCheck(
        id="code_evidence",
        label="Implementation produced concrete code evidence",
        status=status,
        severity="warning",
        evidence=evidence,
    )


def _validation_evidence_check(state: SharedProjectState) -> DeliveryReadinessCheck:
    test_executions = [execution for execution in state.executions if _workitem_kind(state, execution.workitem_id) in TEST_WORKITEM_KINDS]
    passed_validation = [execution.workitem_id for execution in test_executions if execution.validation_success is True]
    failed_validation = [execution.workitem_id for execution in test_executions if execution.validation_success is False]
    harness_runs = [
        execution.workitem_id
        for execution in test_executions
        if execution.source_backend.startswith("cli/") and execution.validation_success is not False
    ]
    if passed_validation:
        status = "pass"
        evidence = "passed validation: " + ", ".join(passed_validation)
    elif failed_validation:
        status = "fail"
        evidence = "failed validation: " + ", ".join(failed_validation)
    elif harness_runs:
        status = "warn"
        evidence = "harness evidence without explicit validation_success=true: " + ", ".join(harness_runs)
    else:
        status = "warn"
        evidence = "no validation execution evidence"
    return DeliveryReadinessCheck(
        id="validation_evidence",
        label="Testing produced validation evidence",
        status=status,
        severity="blocker" if failed_validation else "warning",
        evidence=evidence,
    )


def _scope_contract_check(state: SharedProjectState, artifact_store: ArtifactStore) -> DeliveryReadinessCheck:
    frozen_requirement = _latest_artifact(state, {"frozen_requirement_spec"})
    if frozen_requirement is None:
        return DeliveryReadinessCheck(
            id="scope_contract",
            label="Downstream artifacts stay inside frozen requirement scope",
            status="warn",
            severity="warning",
            evidence="not evaluated because frozen requirement is missing",
        )
    checked = 0
    violations: list[str] = []
    for artifact in state.artifacts:
        if artifact.kind in SKIP_SCOPE_KINDS:
            continue
        checked += 1
        result = evaluate_scope_contract(frozen_requirement, artifact_store.read_content(artifact))
        violations.extend(f"{artifact.id}:{violation.rule_id}" for violation in result.violations)
    if checked == 0:
        status = "warn"
        evidence = "no downstream artifacts to evaluate"
    elif violations:
        status = "fail"
        evidence = "violations: " + ", ".join(violations)
    else:
        status = "pass"
        evidence = f"checked {checked} downstream artifacts"
    return DeliveryReadinessCheck(
        id="scope_contract",
        label="Downstream artifacts stay inside frozen requirement scope",
        status=status,
        severity="blocker" if violations else "warning",
        evidence=evidence,
    )


def _score(checks: list[DeliveryReadinessCheck]) -> int:
    score = 100
    for check in checks:
        if check.status == "fail":
            score -= 35 if check.severity == "blocker" else 20
        elif check.status == "warn":
            score -= 10
    return max(0, score)


def _status(checks: list[DeliveryReadinessCheck], score: int) -> str:
    if any(check.status == "fail" and check.severity == "blocker" for check in checks):
        return "blocked"
    if score >= 85 and all(check.status == "pass" for check in checks):
        return "ready"
    if score >= 70:
        return "at_risk"
    return "incomplete"


def _latest_artifact(state: SharedProjectState, kinds: set[str]) -> Artifact | None:
    for artifact in reversed(state.artifacts):
        if artifact.kind in kinds:
            return artifact
    return None


def _workitem_kind(state: SharedProjectState, workitem_id: str) -> str:
    workitem = next((item for item in state.workitems if item.id == workitem_id), None)
    return workitem.kind if workitem else ""


__all__ = [
    "DeliveryReadinessCheck",
    "DeliveryReadinessResult",
    "evaluate_delivery_readiness",
    "render_delivery_readiness_markdown",
]
