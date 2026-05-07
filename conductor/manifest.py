"""Run manifest writer for reproducible Conductor executions."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path

from conductor.config.cli import CLISelectionConfig
from conductor.config.execution import RunProfile
from conductor.config.llm import LLMRuntimeConfig
from conductor.diagnostics import build_platform_diagnostics
from conductor.domain.models import SharedProjectState
from conductor.requirement_benchmark import build_requirement_case_from_text, evaluate_requirement_document
from conductor.task_center.service import TaskCenterService
from conductor.testing.coverage import evaluate_requirement_coverage


@dataclass(slots=True)
class RunManifest:
    """A compact record of how one project run was executed."""

    schema_version: str
    run_id: str
    project_id: str
    generated_at: str
    completed_at: str
    run_profile: str
    project_root: str
    status: str
    final_status: str
    current_stage: str
    working_directory: str
    selected_cli_names: list[str]
    role_cli_bindings: dict[str, str | None]
    summary: dict[str, object]
    platform_diagnostics: dict[str, object]
    agents: list[dict[str, object]]
    executions: list[dict[str, object]]
    cli_runs: list[dict[str, object]]
    llm_runs: list[dict[str, object]]
    collaboration_runs: list[dict[str, object]]
    requirement_evaluations: list[dict[str, object]]
    requirement_coverage_results: list[dict[str, object]]
    workitems: list[dict[str, object]]
    task_assignments: list[dict[str, object]]
    artifacts: list[dict[str, object]]
    artifact_files: list[str]
    files: dict[str, object]
    log_path: str
    report_path: str


class RunManifestWriter:
    """Write project run manifests under the project .conductor directory."""

    def write(
        self,
        state: SharedProjectState,
        cli_config: CLISelectionConfig,
        run_profile: str | RunProfile,
        report_path: str | Path,
        llm_runtime_config: LLMRuntimeConfig | None = None,
    ) -> Path:
        """Write and return the manifest path."""
        path = self._manifest_path(state.project.id, state.project.project_root)
        generated_at = datetime.now(timezone.utc).isoformat()
        log_path = str(self._log_path(state.project.id, state.project.project_root))
        report_path_text = str(Path(report_path))
        executions = [self._execution_record(state, execution, cli_config) for execution in state.executions]
        requirement_evaluations = self._requirement_evaluations(state)
        requirement_coverage_results = self._requirement_coverage_results(state)
        task_center = TaskCenterService(_ManifestStateStore(state))
        platform_diagnostics = build_platform_diagnostics(
            cli_config=cli_config,
            project_root=state.project.project_root or Path.cwd(),
            llm_runtime_config=llm_runtime_config,
            probe_llm=False,
        ).to_dict()
        manifest = RunManifest(
            schema_version="1.9",
            run_id=f"{state.project.id}:{generated_at}",
            project_id=state.project.id,
            generated_at=generated_at,
            completed_at=generated_at,
            run_profile=str(run_profile.value if isinstance(run_profile, RunProfile) else run_profile),
            project_root=state.project.project_root,
            status=state.project_status.value,
            final_status=state.project_status.value,
            current_stage=state.current_stage or "",
            working_directory=state.project.project_root,
            selected_cli_names=list(cli_config.selected_cli_names),
            role_cli_bindings=dict(cli_config.role_cli_bindings),
            summary={
                "final_status": state.project_status.value,
                "workitem_count": len(state.workitems),
                "execution_count": len(state.executions),
                "artifact_count": len(state.artifacts),
                "agent_count": len(state.agent_activations),
                "blocked_count": len(state.blockers),
                "requirement_quality_score": max(
                    [int(item["score"]) for item in requirement_evaluations],
                    default=0,
                ),
                "requirement_coverage_status": self._requirement_coverage_status(requirement_coverage_results),
                "task_center_summary": task_center.summary(state),
            },
            platform_diagnostics=platform_diagnostics,
            agents=[self._agent_record(state, activation, cli_config) for activation in state.agent_activations],
            executions=executions,
            cli_runs=[
                record
                for record in executions
                if str(record.get("source_backend", "")).startswith(("agent_cli/", "cli/"))
            ],
            llm_runs=self._llm_runs(state, executions),
            collaboration_runs=self._collaboration_runs(state),
            requirement_evaluations=requirement_evaluations,
            requirement_coverage_results=requirement_coverage_results,
            workitems=[
                {
                    "id": item.id,
                    "stage": item.stage,
                    "kind": item.kind,
                    "status": item.status.value,
                    "owner_agent": item.owner_agent or "",
                    "failure_type": item.failure_type,
                    "retryable": item.retryable,
                    "failure_summary": item.failure_summary,
                    "acceptance_criteria": list(item.acceptance_criteria),
                }
                for item in state.workitems
            ],
            task_assignments=[
                {
                    "id": assignment.id,
                    "workitem_id": assignment.workitem_id,
                    "role": assignment.role,
                    "status": assignment.status.value,
                    "assigned_agent_id": assignment.assigned_agent_id or "",
                    "claim_reason": assignment.claim_reason,
                    "claimable": task_center.claimable(state, assignment),
                    "unmet_dependency_ids": task_center.unmet_dependency_ids(state, assignment),
                    "dependencies": list(assignment.dependencies),
                    "input_artifact_ids": list(assignment.input_artifact_ids),
                    "output_artifact_ids": list(assignment.output_artifact_ids),
                    "result_summary": assignment.result_summary,
                    "blocked_reason": assignment.blocked_reason or "",
                }
                for assignment in state.task_assignments
            ],
            artifacts=[
                {
                    "id": artifact.id,
                    "kind": artifact.kind,
                    "agent_id": artifact.agent_id,
                    "source_backend": artifact.source_backend,
                    "path": artifact.path or "",
                    "version": artifact.version,
                }
                for artifact in state.artifacts
            ],
            artifact_files=[artifact.path or "" for artifact in state.artifacts if artifact.path],
            files={
                "log": log_path,
                "report": report_path_text,
                "manifest": str(path),
                "artifacts": [artifact.path or "" for artifact in state.artifacts if artifact.path],
            },
            log_path=log_path,
            report_path=report_path_text,
        )
        path.write_text(json.dumps(asdict(manifest), ensure_ascii=False, indent=2), encoding="utf-8")
        return path

    def _requirement_evaluations(self, state: SharedProjectState) -> list[dict[str, object]]:
        """Evaluate requirement-stage artifacts and embed quality scores in the manifest."""
        case = build_requirement_case_from_text(state.project.id, state.project.goal, name="project_requirement")
        evaluations: list[dict[str, object]] = []
        for artifact in state.artifacts:
            if artifact.kind not in {"frozen_requirement_spec", "requirement_spec"}:
                continue
            evaluation = evaluate_requirement_document(artifact.content, case)
            evaluations.append(
                {
                    "artifact_id": artifact.id,
                    "kind": artifact.kind,
                    "path": artifact.path or "",
                    "score": evaluation.score,
                    "passed": evaluation.passed,
                    "checks": evaluation.checks,
                    "metrics": evaluation.metrics,
                    "findings": evaluation.findings,
                }
            )
        return evaluations

    def _requirement_coverage_results(self, state: SharedProjectState) -> list[dict[str, object]]:
        """Evaluate validation executions against the frozen requirement."""
        frozen_requirement = next(
            (artifact for artifact in reversed(state.artifacts) if artifact.kind == "frozen_requirement_spec"),
            None,
        )
        if frozen_requirement is None:
            return []

        validation_workitem_ids = {
            item.id
            for item in state.workitems
            if item.kind in {"acceptance_check", "automated_test", "api_validation", "ui_validation"}
        }
        results: list[dict[str, object]] = []
        for execution in state.executions:
            if execution.workitem_id not in validation_workitem_ids and not execution.validation_command:
                continue
            output = "\n".join(
                [
                    execution.result or "",
                    execution.cli_stdout_tail or "",
                    execution.cli_stderr_tail or "",
                ]
            )
            coverage = evaluate_requirement_coverage(frozen_requirement, output)
            results.append(
                {
                    "workitem_id": execution.workitem_id,
                    "agent_id": execution.agent_id,
                    "status": "pass" if coverage.passed else "missing_coverage",
                    "passed": coverage.passed,
                    "required_rules": [rule.rule_id for rule in coverage.required_rules],
                    "covered_rules": list(coverage.covered_rule_ids),
                    "missing_rules": [rule.rule_id for rule in coverage.missing_rules],
                    "missing_labels": [rule.label for rule in coverage.missing_rules],
                    "traceability": [
                        {
                            "rule_id": item.rule_id,
                            "label": item.label,
                            "status": item.status,
                            "requirement_terms": list(item.requirement_terms),
                            "evidence_terms": list(item.evidence_terms),
                        }
                        for item in coverage.traceability
                    ],
                    "summary": coverage.summary(),
                }
            )
        return results

    def _requirement_coverage_status(self, results: list[dict[str, object]]) -> str:
        """Return a compact manifest summary status for requirement validation coverage."""
        if not results:
            return "not_evaluated"
        if any(not result.get("passed") for result in results):
            return "missing_coverage"
        if any(result.get("required_rules") for result in results):
            return "pass"
        return "no_rules"

    def _manifest_path(self, project_id: str, project_root: str | Path | None) -> Path:
        root = (Path(project_root) / ".conductor" / "manifests") if project_root else Path(".conductor") / "manifests"
        root.mkdir(parents=True, exist_ok=True)
        return root / f"{project_id}.manifest.json"

    def _log_path(self, project_id: str, project_root: str | Path | None) -> Path:
        root = (Path(project_root) / ".conductor" / "logs") if project_root else Path(".conductor_logs")
        return root / f"{project_id}.jsonl"

    def _execution_record(self, state: SharedProjectState, execution, cli_config: CLISelectionConfig) -> dict[str, object]:
        source_backend = execution.source_backend or self._source_backend_for_execution(state, execution.workitem_id)
        cli_name = execution.cli_name or self._cli_from_source_backend(source_backend)
        artifacts = [
            artifact
            for artifact in state.artifacts
            if artifact.workitem_id == execution.workitem_id and artifact.kind != "collaboration_review"
        ]
        return {
            "workitem_id": execution.workitem_id,
            "agent_id": execution.agent_id,
            "status": execution.status.value,
            "source_backend": source_backend,
            "cli_name": cli_name,
            "model": execution.model or self._model_for_cli(cli_name, cli_config),
            "working_directory": execution.working_directory or state.project.project_root,
            "changed_files": list(execution.changed_files),
            "validation_command": list(execution.validation_command),
            "validation_exit_code": execution.validation_exit_code,
            "validation_success": execution.validation_success,
            "failure_type": execution.failure_type,
            "failure_summary": execution.failure_summary,
            "cli_stdout_tail": execution.cli_stdout_tail,
            "cli_stderr_tail": execution.cli_stderr_tail,
            "artifact_ids": [artifact.id for artifact in artifacts],
            "artifact_files": [artifact.path or "" for artifact in artifacts if artifact.path],
        }

    def _agent_record(self, state: SharedProjectState, activation, cli_config: CLISelectionConfig) -> dict[str, object]:
        """Build one agent manifest record from actual runtime evidence."""
        role_cli = self._cli_for_role(activation.role, cli_config)
        execution_records = [item for item in state.executions if item.agent_id == activation.agent_id]
        artifacts = [item for item in state.artifacts if item.agent_id == activation.agent_id]
        reviews = [
            (collaboration, review)
            for collaboration in state.collaborations
            for review in collaboration.contributions
            if review.agent_id == activation.agent_id
        ]
        draft_versions = [
            (collaboration, draft)
            for collaboration in state.collaborations
            for draft in collaboration.draft_versions
            if draft.author_agent_id == activation.agent_id
        ]
        review_runtime = [self._review_runtime_metadata(state, collaboration, review) for collaboration, review in reviews]
        draft_runtime = [self._draft_runtime_metadata(state, collaboration, draft) for collaboration, draft in draft_versions]
        source_backends = self._dedupe(
            [
                *[item.source_backend for item in execution_records if item.source_backend],
                *[item.source_backend for item in artifacts if item.source_backend],
                *[item["source_backend"] for item in review_runtime if item["source_backend"]],
                *[item["source_backend"] for item in draft_runtime if item["source_backend"]],
            ]
        )
        models = self._dedupe(
            [
                *[item.model for item in execution_records if item.model],
                *[self._model_from_source_backend(item.source_backend) for item in artifacts if item.source_backend],
                *[item["model"] for item in review_runtime if item["model"]],
                *[item["model"] for item in draft_runtime if item["model"]],
                self._model_for_cli(role_cli, cli_config),
            ]
        )
        output_files = self._dedupe(
            [
                *[item.path or "" for item in artifacts if item.path],
                *[item["output_path"] for item in review_runtime if item["output_path"]],
                *[item["output_path"] for item in draft_runtime if item["output_path"]],
            ]
        )
        return {
            "agent_id": activation.agent_id,
            "role": activation.role,
            "stage": activation.stage,
            "execution_backend": self._effective_agent_backend(activation.execution_backend, source_backends),
            "source_backends": source_backends,
            "cli_name": role_cli,
            "model": models[0] if models else "",
            "models": models,
            "working_directory": state.project.project_root,
            "reason": activation.reason,
            "workitem_ids": self._dedupe([item.workitem_id for item in execution_records] + [item.workitem_id for item in artifacts]),
            "artifact_ids": self._dedupe([item.id for item in artifacts]),
            "output_files": output_files,
            "review_count": len(reviews),
            "revision_count": len([item for _, item in draft_versions if item.round_index > 0]),
        }

    def _llm_runs(self, state: SharedProjectState, executions: list[dict[str, object]]) -> list[dict[str, object]]:
        """Return all LLM-backed calls known to the run manifest."""
        runs: list[dict[str, object]] = []
        for record in executions:
            source_backend = str(record.get("source_backend", ""))
            if source_backend.startswith(("llm/", "llm_harness/", "llm_harness_code/")):
                runs.append(
                    {
                        "mode": "workitem_execution",
                        "workitem_id": record.get("workitem_id", ""),
                        "agent_id": record.get("agent_id", ""),
                        "source_backend": source_backend,
                        "model": record.get("model") or self._model_from_source_backend(source_backend),
                        "output_files": record.get("artifact_files", []),
                        "status": record.get("status", ""),
                    }
                )
        for collaboration in state.collaborations:
            for review in collaboration.contributions:
                runtime = self._review_runtime_metadata(state, collaboration, review)
                if str(runtime["source_backend"]).startswith(("llm/", "llm_harness/")):
                    runs.append(
                        {
                            "mode": "collaboration_review",
                            "collaboration_id": collaboration.id,
                            "workitem_id": collaboration.workitem_id,
                            "agent_id": review.agent_id,
                            "role": review.role,
                            "phase": review.phase,
                            "round_index": review.round_index,
                            "decision": review.decision.value,
                            "source_backend": runtime["source_backend"],
                            "model": runtime["model"],
                            "output_files": [runtime["output_path"]] if runtime["output_path"] else [],
                            "duration_ms": runtime["duration_ms"],
                        }
                    )
            for draft in collaboration.draft_versions:
                runtime = self._draft_runtime_metadata(state, collaboration, draft)
                if str(runtime["source_backend"]).startswith(("llm/", "llm_harness/")):
                    runs.append(
                        {
                            "mode": "collaboration_revision" if draft.round_index > 0 else "collaboration_initial_draft",
                            "collaboration_id": collaboration.id,
                            "workitem_id": collaboration.workitem_id,
                            "agent_id": draft.author_agent_id,
                            "round_index": draft.round_index,
                            "version": draft.version,
                            "source_backend": runtime["source_backend"],
                            "model": runtime["model"],
                            "output_files": [runtime["output_path"]] if runtime["output_path"] else [],
                            "duration_ms": runtime["duration_ms"],
                            "review_ids": list(draft.review_ids),
                        }
                    )
        return runs

    def _collaboration_runs(self, state: SharedProjectState) -> list[dict[str, object]]:
        """Return structured collaboration sessions."""
        return [
            {
                "id": collaboration.id,
                "workitem_id": collaboration.workitem_id,
                "lead_agent_id": collaboration.lead_agent_id,
                "reviewer_agent_ids": list(collaboration.reviewer_agent_ids),
                "status": collaboration.status.value,
                "team_plan": dict(collaboration.team_plan),
                "max_rounds": collaboration.max_rounds,
                "current_round": collaboration.current_round,
                "review_count": len(collaboration.contributions),
                "draft_version_count": len(collaboration.draft_versions),
                "final_artifact_id": collaboration.final_artifact_id or "",
                "phases": self._dedupe([review.phase for review in collaboration.contributions]),
                "decisions": {
                    "approve": len([review for review in collaboration.contributions if review.decision.value == "approve"]),
                    "request_changes": len(
                        [review for review in collaboration.contributions if review.decision.value == "request_changes"]
                    ),
                },
                "reviews": [
                    {
                        "id": review.id,
                        "round_index": review.round_index,
                        "phase": review.phase,
                        "agent_id": review.agent_id,
                        "role": review.role,
                        "decision": review.decision.value,
                        "source_backend": self._review_runtime_metadata(state, collaboration, review)["source_backend"],
                        "model": self._review_runtime_metadata(state, collaboration, review)["model"],
                        "output_path": self._review_runtime_metadata(state, collaboration, review)["output_path"],
                        "duration_ms": self._review_runtime_metadata(state, collaboration, review)["duration_ms"],
                    }
                    for review in collaboration.contributions
                ],
                "draft_versions": [
                    {
                        "version": draft.version,
                        "round_index": draft.round_index,
                        "author_agent_id": draft.author_agent_id,
                        "review_ids": list(draft.review_ids),
                        "source_backend": self._draft_runtime_metadata(state, collaboration, draft)["source_backend"],
                        "model": self._draft_runtime_metadata(state, collaboration, draft)["model"],
                        "output_path": self._draft_runtime_metadata(state, collaboration, draft)["output_path"],
                        "duration_ms": self._draft_runtime_metadata(state, collaboration, draft)["duration_ms"],
                    }
                    for draft in collaboration.draft_versions
                ],
            }
            for collaboration in state.collaborations
        ]

    def _cli_for_role(self, role: str, cli_config: CLISelectionConfig) -> str:
        cli_name = cli_config.role_cli_bindings.get(role) or ""
        if cli_name in cli_config.selected_cli_names:
            return cli_name
        return ""

    def _model_for_cli(self, cli_name: str, cli_config: CLISelectionConfig) -> str:
        if not cli_name:
            return ""
        if cli_name == "codex":
            return f"{cli_config.codex_model}/{cli_config.codex_reasoning_effort}"
        return "local-cli-config"

    def _model_from_source_backend(self, source_backend: str) -> str:
        """Extract model name from source backend conventions."""
        if source_backend.startswith(("llm_harness/", "llm_harness_code/")):
            return source_backend.split("/", 1)[1]
        if source_backend.startswith("llm/"):
            return source_backend.split("/", 1)[1]
        return ""

    def _review_runtime_metadata(self, state: SharedProjectState, collaboration, review) -> dict[str, object]:
        """Return review runtime metadata with compatibility fallbacks for old state files."""
        source_backend = review.source_backend or self._default_llm_source_backend_for_workitem(state, collaboration.workitem_id)
        output_path = review.output_path or self._existing_review_output_path(state, collaboration, review)
        return {
            "source_backend": source_backend,
            "model": review.model or self._model_from_source_backend(source_backend),
            "output_path": output_path,
            "duration_ms": review.duration_ms,
        }

    def _draft_runtime_metadata(self, state: SharedProjectState, collaboration, draft) -> dict[str, object]:
        """Return draft runtime metadata with compatibility fallbacks for old state files."""
        source_backend = draft.source_backend or self._default_llm_source_backend_for_workitem(state, collaboration.workitem_id)
        output_path = draft.output_path or self._existing_draft_output_path(state, collaboration, draft)
        return {
            "source_backend": source_backend,
            "model": draft.model or self._model_from_source_backend(source_backend),
            "output_path": output_path,
            "duration_ms": draft.duration_ms,
        }

    def _default_llm_source_backend_for_workitem(self, state: SharedProjectState, workitem_id: str) -> str:
        """Infer the likely LLM backend for legacy collaboration records."""
        for execution in reversed(state.executions):
            if execution.workitem_id == workitem_id and execution.source_backend.startswith(("llm/", "llm_harness/")):
                return execution.source_backend
        for artifact in reversed(state.artifacts):
            if artifact.workitem_id == workitem_id and artifact.source_backend.startswith(("llm/", "llm_harness/")):
                return artifact.source_backend
        return ""

    def _existing_review_output_path(self, state: SharedProjectState, collaboration, review) -> str:
        """Infer a collaboration review file path from the current file naming contract."""
        path = (
            Path(state.project.project_root)
            / ".conductor"
            / "llm_outputs"
            / f"{collaboration.workitem_id}.{review.phase}.{review.role}.round-{review.round_index}.review.md"
        )
        return str(path) if path.exists() else ""

    def _existing_draft_output_path(self, state: SharedProjectState, collaboration, draft) -> str:
        """Infer a collaboration draft/revision file path from the current file naming contract."""
        if draft.round_index == 0:
            path = Path(state.project.project_root) / ".conductor" / "llm_outputs" / f"{collaboration.workitem_id}.md"
            return str(path) if path.exists() else ""
        phases = []
        for review_id in draft.review_ids:
            phase = next((review.phase for review in collaboration.contributions if review.id == review_id), "")
            if phase and phase not in phases:
                phases.append(phase)
        for phase in phases or ["cross_functional_review", "design_peer_review"]:
            path = (
                Path(state.project.project_root)
                / ".conductor"
                / "llm_outputs"
                / f"{collaboration.workitem_id}.{phase}.designer.revision.md"
            )
            if path.exists():
                return str(path)
        return ""

    def _effective_agent_backend(self, configured_backend: str, source_backends: list[str]) -> str:
        """Return the effective backend family observed during the run."""
        if any(item.startswith(("llm_harness/", "llm_harness_code/")) for item in source_backends):
            return "llm_harness"
        if any(item.startswith("agent_cli/") for item in source_backends):
            return "agent_cli"
        if any(item.startswith("cli/") for item in source_backends):
            return "harness"
        if any(item.startswith("llm/") for item in source_backends):
            return "llm"
        if any(item == "collaboration" for item in source_backends):
            return "collaboration"
        if any(item.startswith("mock") for item in source_backends):
            return "mock"
        return configured_backend

    def _dedupe(self, values: list[str]) -> list[str]:
        """Return non-empty values while preserving order."""
        result: list[str] = []
        for value in values:
            if value and value not in result:
                result.append(value)
        return result

    def _source_backend_for_execution(self, state: SharedProjectState, workitem_id: str) -> str:
        artifact = next(
            (
                item
                for item in reversed(state.artifacts)
                if item.workitem_id == workitem_id and item.source_backend.startswith(("agent_cli/", "cli/"))
            ),
            None,
        )
        if artifact is None:
            artifact = next((item for item in reversed(state.artifacts) if item.workitem_id == workitem_id), None)
        return artifact.source_backend if artifact else ""

    def _cli_from_source_backend(self, source_backend: str) -> str:
        if source_backend.startswith("agent_cli/"):
            return source_backend.split("/", 1)[1]
        if source_backend.startswith("cli/"):
            return source_backend.split("/", 1)[1]
        return ""


class _ManifestStateStore:
    """Read-only state adapter for manifest-time Task Center calculations."""

    def __init__(self, state: SharedProjectState) -> None:
        self.state = state

    def get_state(self, project_id: str) -> SharedProjectState:
        if self.state.project.id != project_id:
            raise KeyError(project_id)
        return self.state


__all__ = ["RunManifest", "RunManifestWriter"]
