"""Run manifest writer for reproducible Conductor executions."""

from __future__ import annotations

import json
import os
import platform
import sys
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path

from conductor.artifacts.scope_contract import evaluate_scope_contract
from conductor.artifacts.store import ArtifactStore
from conductor.config.cli import CLISelectionConfig
from conductor.config.execution import RunProfile
from conductor.config.llm import LLMRuntimeConfig
from conductor.diagnostics import build_platform_diagnostics
from conductor.domain.models import SharedProjectState
from conductor.execution.failure_policy import remediation_suggestions
from conductor.manifest_schema import RUN_MANIFEST_SCHEMA_VERSION
from conductor.preflight_gate import read_preflight_gate
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
    resume_cursor: dict[str, object]
    run_environment: dict[str, object]
    platform_diagnostics: dict[str, object]
    agents: list[dict[str, object]]
    executions: list[dict[str, object]]
    cli_runs: list[dict[str, object]]
    llm_runs: list[dict[str, object]]
    collaboration_runs: list[dict[str, object]]
    retry_history: list[dict[str, object]]
    requirement_evaluations: list[dict[str, object]]
    requirement_coverage_results: list[dict[str, object]]
    scope_contract_results: list[dict[str, object]]
    workitems: list[dict[str, object]]
    task_assignments: list[dict[str, object]]
    artifacts: list[dict[str, object]]
    artifact_files: list[str]
    task_prompt_files: list[str]
    files: dict[str, object]
    log_path: str
    report_path: str


class RunManifestWriter:
    """Write project run manifests under the project .conductor directory."""

    SECRET_ARG_MARKERS = ("key", "token", "secret", "password")

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
        cli_runs = [
            record for record in executions if str(record.get("source_backend", "")).startswith(("agent_cli/", "cli/"))
        ]
        collaboration_runs = self._collaboration_runs(state)
        retry_history = self._retry_history(state)
        requirement_evaluations = self._requirement_evaluations(state)
        requirement_coverage_results = self._requirement_coverage_results(state)
        scope_contract_results = self._scope_contract_results(state)
        task_center = TaskCenterService(_ManifestStateStore(state))
        artifact_files = [artifact.path or "" for artifact in state.artifacts if artifact.path]
        task_prompt_files = self._dedupe([assignment.prompt_file for assignment in state.task_assignments])
        preflight_gate = read_preflight_gate(state.project.project_root)
        platform_diagnostics = build_platform_diagnostics(
            cli_config=cli_config,
            project_root=state.project.project_root or Path.cwd(),
            llm_runtime_config=llm_runtime_config,
            probe_llm=False,
        ).to_dict()
        llm_context_windows = self._llm_context_windows(platform_diagnostics)
        llm_runs = self._llm_runs(state, executions, llm_context_windows)
        llm_token_usage = self._sum_token_usage([dict(run.get("token_usage", {})) for run in llm_runs])
        llm_cost_estimate = self._llm_cost_estimate(llm_runs, llm_runtime_config)
        resume_cursor = self._resume_cursor(state)
        manifest = RunManifest(
            schema_version=RUN_MANIFEST_SCHEMA_VERSION,
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
                "scope_contract_status": self._scope_contract_status(scope_contract_results),
                "scope_contract_violation_count": sum(
                    len(item.get("violations", [])) for item in scope_contract_results
                ),
                "task_center_summary": task_center.summary(state),
                "workitem_status_counts": self._workitem_status_counts(state),
                "execution_status_counts": self._execution_status_counts(executions),
                "failed_workitem_ids": self._failed_workitem_ids(state),
                "blocked_reasons": list(state.blockers),
                "retryable_failure_count": self._retryable_failure_count(state),
                "non_retryable_failure_count": self._non_retryable_failure_count(state),
                "retry_history_count": len(retry_history),
                "retry_attempt_count": sum(int(item.get("retry_count", 0)) for item in retry_history),
                "cli_run_count": len(cli_runs),
                "llm_run_count": len(llm_runs),
                "llm_token_usage": llm_token_usage,
                "llm_cost_estimate": llm_cost_estimate,
                "llm_context_windows": llm_context_windows,
                "collaboration_run_count": len(collaboration_runs),
                "changed_file_count": len(self._changed_files(executions)),
                "changed_files": self._changed_files(executions),
                "artifact_file_count": len(artifact_files),
                "task_prompt_file_count": len(task_prompt_files),
                "validation_failure_count": self._validation_failure_count(executions),
                "preflight_gate_ok": preflight_gate.ok,
                "preflight_gate_errors": preflight_gate.errors,
                "preflight_gate_recommendations": preflight_gate.recommendations,
            },
            resume_cursor=resume_cursor,
            run_environment=self._run_environment_snapshot(),
            platform_diagnostics=platform_diagnostics,
            agents=[self._agent_record(state, activation, cli_config) for activation in state.agent_activations],
            executions=executions,
            cli_runs=cli_runs,
            llm_runs=llm_runs,
            collaboration_runs=collaboration_runs,
            retry_history=retry_history,
            requirement_evaluations=requirement_evaluations,
            requirement_coverage_results=requirement_coverage_results,
            scope_contract_results=scope_contract_results,
            workitems=[
                {
                    "id": item.id,
                    "stage": item.stage,
                    "kind": item.kind,
                    "status": item.status.value,
                    "owner_agent": item.owner_agent or "",
                    "retry_count": item.retry_count,
                    "max_retries": item.max_retries,
                    "blocked_reason": item.blocked_reason or "",
                    "failure_type": item.failure_type,
                    "retryable": item.retryable,
                    "failure_summary": item.failure_summary,
                    "remediation_suggestions": remediation_suggestions(
                        item.failure_type,
                        retryable=item.retryable,
                        summary=item.failure_summary or item.blocked_reason or "",
                    )
                    if item.failure_type or item.blocked_reason
                    else [],
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
                    "claim_token": assignment.claim_token,
                    "claim_reason": assignment.claim_reason,
                    "claimable": task_center.claimable(state, assignment),
                    "unmet_dependency_ids": task_center.unmet_dependency_ids(state, assignment),
                    "dependencies": list(assignment.dependencies),
                    "input_artifact_ids": list(assignment.input_artifact_ids),
                    "output_artifact_ids": list(assignment.output_artifact_ids),
                    "result_summary": assignment.result_summary,
                    "blocked_reason": assignment.blocked_reason or "",
                    "claimed_at": assignment.claimed_at,
                    "claimed_age_seconds": task_center.claimed_age_seconds(assignment),
                    "last_heartbeat_at": assignment.last_heartbeat_at,
                    "heartbeat_age_seconds": task_center.heartbeat_age_seconds(assignment),
                    "stale_claimed": task_center.stale_claimed(assignment),
                    "returned_at": assignment.returned_at,
                    "prompt_file": assignment.prompt_file,
                }
                for assignment in state.task_assignments
            ],
            artifacts=[
                {
                    "id": artifact.id,
                    "project_id": artifact.project_id,
                    "workitem_id": artifact.workitem_id,
                    "title": artifact.title,
                    "kind": artifact.kind,
                    "agent_id": artifact.agent_id,
                    "source_backend": artifact.source_backend,
                    "path": artifact.path or "",
                    "version": artifact.version,
                    "parent_artifact_id": artifact.parent_artifact_id or "",
                    "derived_from": list(artifact.derived_from),
                    "review_of": artifact.review_of or "",
                    "collaboration_session_id": artifact.collaboration_session_id or "",
                }
                for artifact in state.artifacts
            ],
            artifact_files=artifact_files,
            task_prompt_files=task_prompt_files,
            files={
                "log": log_path,
                "report": report_path_text,
                "manifest": str(path),
                "artifacts": artifact_files,
                "task_prompts": task_prompt_files,
                "preflight_gate": preflight_gate.path,
            },
            log_path=log_path,
            report_path=report_path_text,
        )
        path.write_text(json.dumps(asdict(manifest), ensure_ascii=False, indent=2), encoding="utf-8")
        return path

    def _run_environment_snapshot(self) -> dict[str, object]:
        """Return a non-secret runtime snapshot for replay and audit."""
        safe_env_keys = [
            "PYTHONUTF8",
            "PYTHONIOENCODING",
            "LANG",
            "LC_ALL",
            "VIRTUAL_ENV",
            "CONDA_PREFIX",
            "UV_PROJECT_ENVIRONMENT",
            "COMSPEC",
            "SHELL",
        ]
        environment = {key: os.environ.get(key, "") for key in safe_env_keys if os.environ.get(key)}
        path_value = os.environ.get("PATH", "")
        return {
            "python_executable": sys.executable,
            "python_version": platform.python_version(),
            "implementation": platform.python_implementation(),
            "platform": platform.platform(),
            "system": platform.system(),
            "machine": platform.machine(),
            "process_cwd": str(Path.cwd()),
            "command_argv": self._redacted_command_argv(list(sys.argv)),
            "path_entries_count": len([entry for entry in path_value.split(os.pathsep) if entry]),
            "environment": environment,
        }

    def _redacted_command_argv(self, argv: list[str]) -> list[str]:
        """Return command argv with secret argument values redacted."""
        redacted: list[str] = []
        redact_next = False
        for item in argv:
            if redact_next:
                redacted.append("<redacted>")
                redact_next = False
                continue
            if "=" in item:
                name, _ = item.split("=", 1)
                if self._is_secret_arg_name(name):
                    redacted.append(f"{name}=<redacted>")
                    continue
                redacted.append(item)
                continue
            redacted.append(item)
            if self._is_secret_arg_name(item):
                redact_next = True
        return redacted

    def _is_secret_arg_name(self, name: str) -> bool:
        """Return whether a command argument name likely carries a secret value."""
        normalized = name.strip().lower().replace("_", "-")
        if not normalized.startswith("-"):
            return False
        return any(marker in normalized for marker in self.SECRET_ARG_MARKERS)

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

    def _llm_context_windows(self, platform_diagnostics: dict[str, object]) -> list[dict[str, object]]:
        """Return compact LLM context window records for manifest consumers."""
        records: list[dict[str, object]] = []
        llm_backends = platform_diagnostics.get("llm_backends", [])
        if not isinstance(llm_backends, list):
            return records
        for backend in llm_backends:
            if not isinstance(backend, dict):
                continue
            context_length = backend.get("context_length")
            if isinstance(context_length, str) and context_length.isdigit():
                context_length = int(context_length)
            elif not isinstance(context_length, int):
                context_length = None
            records.append(
                {
                    "backend": str(backend.get("backend", "")),
                    "model": str(backend.get("model", "")),
                    "enabled": bool(backend.get("enabled", False)),
                    "server_status": str(backend.get("server_status", "")),
                    "health_status": str(backend.get("health_status", "")),
                    "selected_model_available": backend.get("selected_model_available"),
                    "context_length": context_length,
                }
            )
        return records

    def _context_length_for_model(
        self,
        model: str,
        llm_context_windows: list[dict[str, object]],
    ) -> int | None:
        """Return a known context length for a model from diagnostics records."""
        if not model:
            return None
        for record in llm_context_windows:
            if str(record.get("model", "")) != model:
                continue
            context_length = record.get("context_length")
            return context_length if isinstance(context_length, int) else None
        return None

    def _scope_contract_results(self, state: SharedProjectState) -> list[dict[str, object]]:
        """Evaluate downstream artifacts against frozen requirement hard exclusions."""
        frozen_requirement = next(
            (artifact for artifact in reversed(state.artifacts) if artifact.kind == "frozen_requirement_spec"),
            None,
        )
        if frozen_requirement is None:
            return []
        artifact_store = ArtifactStore()
        skipped_kinds = {"requirement_spec", "frozen_requirement_spec", "collaboration_review"}
        results: list[dict[str, object]] = []
        for artifact in state.artifacts:
            if artifact.kind in skipped_kinds:
                continue
            contract = evaluate_scope_contract(frozen_requirement, artifact_store.read_content(artifact))
            results.append(
                {
                    "artifact_id": artifact.id,
                    "workitem_id": artifact.workitem_id,
                    "kind": artifact.kind,
                    "path": artifact.path or "",
                    "passed": contract.passed,
                    "rule_ids": [rule.rule_id for rule in contract.rules],
                    "violations": [
                        {
                            "rule_id": violation.rule_id,
                            "label": violation.label,
                            "evidence": violation.evidence,
                        }
                        for violation in contract.violations
                    ],
                    "summary": contract.summary(),
                }
            )
        return results

    def _scope_contract_status(self, results: list[dict[str, object]]) -> str:
        """Return a compact manifest summary status for downstream scope checks."""
        if not results:
            return "not_evaluated"
        if any(not result.get("passed") for result in results):
            return "violation"
        if any(result.get("rule_ids") for result in results):
            return "pass"
        return "no_rules"

    def _workitem_status_counts(self, state: SharedProjectState) -> dict[str, int]:
        """Return WorkItem status counts for quick run audits."""
        return self._count_values([item.status.value for item in state.workitems])

    def _execution_status_counts(self, executions: list[dict[str, object]]) -> dict[str, int]:
        """Return Execution status counts for quick run audits."""
        return self._count_values([str(item.get("status", "")) for item in executions])

    def _failed_workitem_ids(self, state: SharedProjectState) -> list[str]:
        """Return failed WorkItem ids in manifest order."""
        return [item.id for item in state.workitems if item.status.value == "failed"]

    def _retryable_failure_count(self, state: SharedProjectState) -> int:
        """Return retryable failed WorkItem count."""
        return len([item for item in state.workitems if item.status.value == "failed" and item.retryable])

    def _non_retryable_failure_count(self, state: SharedProjectState) -> int:
        """Return non-retryable failed WorkItem count."""
        return len([item for item in state.workitems if item.status.value == "failed" and not item.retryable])

    def _retry_history(self, state: SharedProjectState) -> list[dict[str, object]]:
        """Return structured retry and exhausted-failure evidence per WorkItem."""
        history: list[dict[str, object]] = []
        for item in state.workitems:
            should_record = item.retry_count > 0 or item.status.value == "failed" or bool(item.blocked_reason)
            if not should_record:
                continue
            related_events = [
                event
                for event in state.recent_events
                if item.id in event and self._looks_like_retry_or_failure_event(event)
            ]
            related_gate_history = [
                gate for gate in state.gate_history if gate.startswith(f"{item.stage}:")
            ]
            history.append(
                {
                    "workitem_id": item.id,
                    "stage": item.stage,
                    "kind": item.kind,
                    "status": item.status.value,
                    "owner_agent": item.owner_agent or "",
                    "retry_count": item.retry_count,
                    "max_retries": item.max_retries,
                    "retry_exhausted": item.retry_count >= item.max_retries and item.status.value == "failed",
                    "retryable": item.retryable,
                    "failure_type": item.failure_type,
                    "failure_summary": item.failure_summary,
                    "blocked_reason": item.blocked_reason or "",
                    "related_events": related_events,
                    "related_gate_history": related_gate_history,
                }
            )
        return history

    def _looks_like_retry_or_failure_event(self, event: str) -> bool:
        """Return whether an event is useful for retry audit trails."""
        normalized = event.lower()
        markers = [
            "retry",
            "重试",
            "failed",
            "failure",
            "失败",
            "blocked",
            "阻塞",
            "gate",
            "门禁",
        ]
        return any(marker in normalized for marker in markers)

    def _changed_files(self, executions: list[dict[str, object]]) -> list[str]:
        """Return deduplicated changed files referenced by executions."""
        changed: list[str] = []
        for execution in executions:
            changed.extend([str(item) for item in execution.get("changed_files", []) if item])
        return self._dedupe(changed)

    def _validation_failure_count(self, executions: list[dict[str, object]]) -> int:
        """Return executions with explicit failed post-edit validation."""
        return len([item for item in executions if item.get("validation_success") is False])

    def _count_values(self, values: list[str]) -> dict[str, int]:
        """Count non-empty string values while preserving first-seen order."""
        counts: dict[str, int] = {}
        for value in values:
            if not value:
                continue
            counts[value] = counts.get(value, 0) + 1
        return counts

    def _resume_cursor(self, state: SharedProjectState) -> dict[str, object]:
        """Return a compact cursor for resuming controller-driven execution."""
        current_stage = state.current_stage or ""
        stage_workitems = [item for item in state.workitems if item.stage == current_stage] if current_stage else []
        pending = [item for item in stage_workitems if item.status.value == "pending"]
        running = [item for item in stage_workitems if item.status.value == "running"]
        retryable_failed = [
            item
            for item in stage_workitems
            if item.status.value == "failed" and item.retryable and item.retry_count < item.max_retries
        ]
        terminal_failed = [
            item
            for item in stage_workitems
            if item.status.value == "failed" and (not item.retryable or item.retry_count >= item.max_retries)
        ]
        if state.project_status.value == "completed":
            next_action = "complete"
        elif state.project_status.value == "blocked" or state.blockers or terminal_failed:
            next_action = "blocked"
        elif running:
            next_action = "inspect_running"
        elif pending:
            next_action = "execute_pending"
        elif retryable_failed:
            next_action = "retry_failed"
        else:
            next_action = "advance_or_wait"
        return {
            "project_id": state.project.id,
            "project_status": state.project_status.value,
            "current_stage": current_stage,
            "next_action": next_action,
            "terminal": state.project_status.value in {"completed", "blocked"},
            "blocked": bool(state.blockers or terminal_failed),
            "blockers": list(state.blockers),
            "next_pending_workitem_ids": [item.id for item in pending],
            "running_workitem_ids": [item.id for item in running],
            "retryable_failed_workitem_ids": [item.id for item in retryable_failed],
            "terminal_failed_workitem_ids": [item.id for item in terminal_failed],
            "completed_workitem_ids": [item.id for item in state.workitems if item.status.value == "done"],
            "last_execution_workitem_id": state.executions[-1].workitem_id if state.executions else "",
            "last_event": state.recent_events[-1] if state.recent_events else "",
        }

    def _normalize_token_usage(self, usage: object) -> dict[str, int]:
        """Return token usage with only integer values."""
        if not isinstance(usage, dict):
            return {}
        normalized: dict[str, int] = {}
        for key, value in usage.items():
            if isinstance(value, int):
                normalized[str(key)] = value
            elif isinstance(value, str) and value.isdigit():
                normalized[str(key)] = int(value)
        return normalized

    def _sum_token_usage(self, usages: list[dict[str, object]]) -> dict[str, int]:
        """Aggregate known token usage fields across LLM runs."""
        totals: dict[str, int] = {}
        for usage in usages:
            for key, value in self._normalize_token_usage(usage).items():
                totals[key] = totals.get(key, 0) + value
        return totals

    def _llm_cost_estimate(
        self,
        llm_runs: list[dict[str, object]],
        llm_runtime_config: LLMRuntimeConfig | None,
    ) -> dict[str, object]:
        """Estimate LLM cost from actual token usage and configured model rates."""
        pricing = getattr(llm_runtime_config, "pricing", None)
        currency = str(getattr(pricing, "currency", "USD") or "USD")
        rate_table = dict(getattr(pricing, "per_million_tokens", {}) or {})
        usage_by_model: dict[str, dict[str, int]] = {}
        for run in llm_runs:
            usage = self._normalize_token_usage(run.get("token_usage", {}))
            if not usage:
                continue
            model = str(run.get("model", "") or self._model_from_source_backend(str(run.get("source_backend", ""))) or "unknown")
            model_usage = usage_by_model.setdefault(model, {})
            for key, value in usage.items():
                model_usage[key] = model_usage.get(key, 0) + value

        if not rate_table:
            return {
                "configured": False,
                "currency": currency,
                "estimated_total": 0.0,
                "model_costs": [],
                "unpriced_models": sorted(usage_by_model),
            }

        model_costs: list[dict[str, object]] = []
        unpriced_models: list[str] = []
        estimated_total = 0.0
        for model, usage in usage_by_model.items():
            rates = self._pricing_rates_for_model(model, rate_table)
            if not rates:
                unpriced_models.append(model)
                continue
            cost, priced_token_types, unpriced_token_types = self._estimate_model_cost(usage, rates)
            estimated_total += cost
            model_costs.append(
                {
                    "model": model,
                    "token_usage": usage,
                    "rates_per_million_tokens": rates,
                    "estimated_cost": round(cost, 8),
                    "priced_token_types": priced_token_types,
                    "unpriced_token_types": unpriced_token_types,
                }
            )
        return {
            "configured": True,
            "currency": currency,
            "estimated_total": round(estimated_total, 8),
            "model_costs": model_costs,
            "unpriced_models": sorted(unpriced_models),
        }

    def _pricing_rates_for_model(
        self,
        model: str,
        rate_table: dict[str, dict[str, float]],
    ) -> dict[str, float]:
        """Return model-specific rates or a wildcard fallback."""
        rates = rate_table.get(model) or rate_table.get(model.lower()) or rate_table.get("*") or {}
        normalized: dict[str, float] = {}
        for key, value in rates.items():
            try:
                normalized[str(key)] = float(value)
            except (TypeError, ValueError):
                continue
        return normalized

    def _estimate_model_cost(
        self,
        usage: dict[str, int],
        rates: dict[str, float],
    ) -> tuple[float, list[str], list[str]]:
        """Estimate one model cost using per-million-token rates."""
        cost = 0.0
        priced: list[str] = []
        unpriced: list[str] = []
        prompt_rate = rates.get("prompt_tokens", rates.get("input_tokens"))
        completion_rate = rates.get("completion_tokens", rates.get("output_tokens"))
        if prompt_rate is not None or completion_rate is not None:
            if "prompt_tokens" in usage and prompt_rate is not None:
                cost += usage["prompt_tokens"] * prompt_rate / 1_000_000
                priced.append("prompt_tokens")
            elif "prompt_tokens" in usage:
                unpriced.append("prompt_tokens")
            if "completion_tokens" in usage and completion_rate is not None:
                cost += usage["completion_tokens"] * completion_rate / 1_000_000
                priced.append("completion_tokens")
            elif "completion_tokens" in usage:
                unpriced.append("completion_tokens")
            return cost, priced, unpriced

        total_rate = rates.get("total_tokens")
        if "total_tokens" in usage and total_rate is not None:
            cost += usage["total_tokens"] * total_rate / 1_000_000
            priced.append("total_tokens")
        elif "total_tokens" in usage:
            unpriced.append("total_tokens")
        return cost, priced, unpriced

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
            "execution_command": list(execution.execution_command),
            "execution_exit_code": execution.execution_exit_code,
            "execution_duration_ms": execution.execution_duration_ms,
            "prompt_hash": execution.prompt_hash,
            "token_usage": self._normalize_token_usage(execution.token_usage),
            "input_artifact_ids": list(execution.input_artifact_ids),
            "changed_files": list(execution.changed_files),
            "validation_command": list(execution.validation_command),
            "validation_exit_code": execution.validation_exit_code,
            "validation_success": execution.validation_success,
            "failure_type": execution.failure_type,
            "failure_summary": execution.failure_summary,
            "remediation_suggestions": remediation_suggestions(
                execution.failure_type,
                retryable=True,
                summary=execution.failure_summary or execution.cli_stderr_tail or execution.cli_stdout_tail,
            )
            if execution.failure_type or execution.failure_summary
            else [],
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

    def _llm_runs(
        self,
        state: SharedProjectState,
        executions: list[dict[str, object]],
        llm_context_windows: list[dict[str, object]] | None = None,
    ) -> list[dict[str, object]]:
        """Return all LLM-backed calls known to the run manifest."""
        runs: list[dict[str, object]] = []
        context_windows = llm_context_windows or []
        for record in executions:
            source_backend = str(record.get("source_backend", ""))
            if source_backend.startswith(("llm/", "llm_harness/", "llm_harness_code/")):
                model = str(record.get("model") or self._model_from_source_backend(source_backend))
                runs.append(
                    {
                        "mode": "workitem_execution",
                        "workitem_id": record.get("workitem_id", ""),
                        "agent_id": record.get("agent_id", ""),
                        "source_backend": source_backend,
                        "model": model,
                        "context_length": self._context_length_for_model(model, context_windows),
                        "token_usage": self._normalize_token_usage(record.get("token_usage", {})),
                        "prompt_hash": record.get("prompt_hash", ""),
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
                            "context_length": self._context_length_for_model(runtime["model"], context_windows),
                            "token_usage": self._normalize_token_usage(runtime["token_usage"]),
                            "prompt_hash": "",
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
                            "context_length": self._context_length_for_model(runtime["model"], context_windows),
                            "token_usage": self._normalize_token_usage(runtime["token_usage"]),
                            "prompt_hash": "",
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
                        "token_usage": self._review_runtime_metadata(state, collaboration, review)["token_usage"],
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
                        "token_usage": self._draft_runtime_metadata(state, collaboration, draft)["token_usage"],
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
            "token_usage": self._normalize_token_usage(getattr(review, "token_usage", {})),
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
            "token_usage": self._normalize_token_usage(getattr(draft, "token_usage", {})),
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
