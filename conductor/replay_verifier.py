"""Read-only verification for Conductor run manifests."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from conductor.manifest_schema import RUN_MANIFEST_SCHEMA_VERSION


@dataclass(slots=True)
class ManifestVerificationResult:
    """Structured result for a manifest self-consistency check."""

    manifest_path: str
    project_id: str = ""
    schema_version: str = ""
    passed: bool = False
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, object]:
        """Return a JSON-serializable result payload."""
        return {
            "manifest_path": self.manifest_path,
            "project_id": self.project_id,
            "schema_version": self.schema_version,
            "passed": self.passed,
            "error_count": len(self.errors),
            "warning_count": len(self.warnings),
            "errors": list(self.errors),
            "warnings": list(self.warnings),
        }


class ManifestVerifier:
    """Validate that one run manifest is internally consistent.

    This verifier intentionally does not replay executions. It checks the
    archived facts that later replay/resume tooling depends on: ids, summary
    counts, resume cursor references, execution/artifact links, and file paths.
    """

    REQUIRED_TOP_LEVEL_FIELDS = (
        "schema_version",
        "project_id",
        "final_status",
        "current_stage",
        "summary",
        "resume_cursor",
        "workitems",
        "executions",
        "artifacts",
        "artifact_files",
        "files",
    )

    SUMMARY_LIST_COUNTS = {
        "workitem_count": "workitems",
        "execution_count": "executions",
        "artifact_count": "artifacts",
        "agent_count": "agents",
        "artifact_file_count": "artifact_files",
        "task_prompt_file_count": "task_prompt_files",
        "cli_run_count": "cli_runs",
        "llm_run_count": "llm_runs",
        "collaboration_run_count": "collaboration_runs",
        "retry_history_count": "retry_history",
    }

    CURSOR_WORKITEM_LISTS = (
        "next_pending_workitem_ids",
        "running_workitem_ids",
        "retryable_failed_workitem_ids",
        "terminal_failed_workitem_ids",
        "completed_workitem_ids",
    )

    SECRET_FIELD_NAMES = {
        "api_key",
        "apikey",
        "api-key",
        "authorization",
        "x-api-key",
        "password",
        "secret",
        "access_token",
        "refresh_token",
    }

    SECRET_VALUE_PATTERNS = (
        re.compile(r"\bsk-[A-Za-z0-9][A-Za-z0-9_-]{10,}\b"),
        re.compile(r"\bBearer\s+[A-Za-z0-9._~+/=-]{10,}\b", re.IGNORECASE),
    )

    def verify(self, manifest_path: str | Path, *, check_files: bool = True) -> ManifestVerificationResult:
        """Verify one manifest file and return a structured report."""
        path = Path(manifest_path)
        result = ManifestVerificationResult(manifest_path=str(path))
        payload = self._load_json(path, result)
        if payload is None:
            result.passed = False
            return result

        result.project_id = str(payload.get("project_id", ""))
        result.schema_version = str(payload.get("schema_version", ""))
        self._verify_required_fields(payload, result)
        self._verify_types(payload, result)
        self._verify_status_consistency(payload, result)
        self._verify_summary_counts(payload, result)
        self._verify_resume_cursor(payload, result)
        self._verify_links(payload, result)
        self._verify_no_secret_leaks(payload, result)
        if check_files:
            self._verify_files(path, payload, result)
        result.passed = not result.errors
        return result

    def _load_json(self, path: Path, result: ManifestVerificationResult) -> dict[str, Any] | None:
        if not path.exists():
            result.errors.append(f"manifest file does not exist: {path}")
            return None
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            result.errors.append(f"manifest is not valid JSON: {exc}")
            return None
        if not isinstance(payload, dict):
            result.errors.append("manifest root must be a JSON object")
            return None
        return payload

    def _verify_required_fields(self, payload: dict[str, Any], result: ManifestVerificationResult) -> None:
        for field_name in self.REQUIRED_TOP_LEVEL_FIELDS:
            if field_name not in payload:
                result.errors.append(f"missing top-level field: {field_name}")
        if not str(payload.get("schema_version", "")):
            result.errors.append("schema_version must be non-empty")
        elif str(payload.get("schema_version", "")) != RUN_MANIFEST_SCHEMA_VERSION:
            result.warnings.append(
                f"manifest schema_version {payload.get('schema_version')} differs from current {RUN_MANIFEST_SCHEMA_VERSION}"
            )
        if not str(payload.get("project_id", "")):
            result.errors.append("project_id must be non-empty")

    def _verify_types(self, payload: dict[str, Any], result: ManifestVerificationResult) -> None:
        for key in (
            "workitems",
            "executions",
            "artifacts",
            "artifact_files",
            "task_prompt_files",
            "cli_runs",
            "llm_runs",
            "collaboration_runs",
            "retry_history",
            "task_assignments",
            "selected_cli_names",
        ):
            if key in payload and not isinstance(payload.get(key), list):
                result.errors.append(f"{key} must be a list")
        for key in ("summary", "resume_cursor", "files", "role_cli_bindings"):
            if key in payload and not isinstance(payload.get(key), dict):
                result.errors.append(f"{key} must be an object")
        self._verify_cli_config(payload, result)

    def _verify_cli_config(self, payload: dict[str, Any], result: ManifestVerificationResult) -> None:
        selected_cli_names = self._string_list(payload.get("selected_cli_names", []))
        role_cli_bindings = payload.get("role_cli_bindings", {})
        if not isinstance(role_cli_bindings, dict):
            return
        selected = set(selected_cli_names)
        for role, cli_name in role_cli_bindings.items():
            if cli_name in ("", None):
                continue
            if not isinstance(cli_name, str):
                result.warnings.append(f"role_cli_bindings.{role} must be a string or null")
                continue
            if selected and cli_name not in selected:
                result.warnings.append(f"role_cli_bindings.{role} references unselected CLI: {cli_name}")

    def _verify_status_consistency(self, payload: dict[str, Any], result: ManifestVerificationResult) -> None:
        status = str(payload.get("status", ""))
        final_status = str(payload.get("final_status", ""))
        if status and final_status and status != final_status:
            result.errors.append("manifest.status does not match manifest.final_status")

        summary = self._dict(payload.get("summary"))
        if summary:
            summary_final_status = str(summary.get("final_status", ""))
            if summary_final_status and final_status and summary_final_status != final_status:
                result.errors.append("summary.final_status does not match manifest.final_status")

    def _verify_summary_counts(self, payload: dict[str, Any], result: ManifestVerificationResult) -> None:
        summary = self._dict(payload.get("summary"))
        if not summary:
            return
        for summary_key, list_key in self.SUMMARY_LIST_COUNTS.items():
            if summary_key not in summary:
                continue
            items = self._list(payload.get(list_key))
            expected = len(items)
            actual = self._as_int(summary.get(summary_key))
            if actual is None:
                result.errors.append(f"summary.{summary_key} must be an integer")
            elif actual != expected:
                result.errors.append(
                    f"summary.{summary_key}={actual} does not match len({list_key})={expected}"
                )
        self._verify_summary_status_counts(
            summary,
            "workitem_status_counts",
            self._status_counts(self._list(payload.get("workitems"))),
            result,
        )
        self._verify_summary_status_counts(
            summary,
            "execution_status_counts",
            self._status_counts(self._list(payload.get("executions"))),
            result,
        )
        self._verify_summary_failure_counts(summary, self._list(payload.get("workitems")), result)
        self._verify_summary_retry_attempt_count(summary, self._list(payload.get("retry_history")), result)
        self._verify_task_center_summary(summary, self._list(payload.get("task_assignments")), result)
        self._verify_summary_blockers(summary, self._dict(payload.get("resume_cursor")), result)
        self._verify_summary_validation_failure_count(summary, self._list(payload.get("executions")), result)
        self._verify_requirement_quality_score(summary, self._list(payload.get("requirement_evaluations")), result)
        self._verify_requirement_coverage_status(summary, self._list(payload.get("requirement_coverage_results")), result)
        self._verify_scope_contract_summary(summary, self._list(payload.get("scope_contract_results")), result)
        self._verify_llm_context_windows(summary, self._list(payload.get("llm_runs")), result)

        changed_files = self._list(summary.get("changed_files"))
        if "changed_files" in summary and not isinstance(summary.get("changed_files"), list):
            result.warnings.append("summary.changed_files must be a list")
        if "changed_file_count" in summary:
            actual = self._as_int(summary.get("changed_file_count"))
            if actual is None:
                result.errors.append("summary.changed_file_count must be an integer")
            elif actual != len(changed_files):
                result.errors.append(
                    f"summary.changed_file_count={actual} does not match len(summary.changed_files)={len(changed_files)}"
                )
        self._verify_summary_changed_files(summary, self._list(payload.get("executions")), result)
        if "llm_token_usage" in summary:
            self._verify_token_usage(summary.get("llm_token_usage"), "summary.llm_token_usage", result)
        if "llm_cost_estimate" in summary:
            self._verify_llm_cost_estimate(summary.get("llm_cost_estimate"), "summary.llm_cost_estimate", result)

    def _verify_summary_changed_files(
        self,
        summary: dict[str, Any],
        executions: list[Any],
        result: ManifestVerificationResult,
    ) -> None:
        if "changed_files" not in summary or not isinstance(summary.get("changed_files"), list):
            return
        actual_changed_files = self._string_list(summary.get("changed_files"))
        expected_changed_files: list[str] = []
        for execution in executions:
            if not isinstance(execution, dict):
                continue
            expected_changed_files.extend(self._string_list(execution.get("changed_files", [])))
        expected_changed_files = self._dedupe(expected_changed_files)
        if actual_changed_files != expected_changed_files:
            result.errors.append(
                f"summary.changed_files={actual_changed_files} does not match execution changed_files={expected_changed_files}"
            )

    def _verify_summary_status_counts(
        self,
        summary: dict[str, Any],
        summary_key: str,
        expected_counts: dict[str, int],
        result: ManifestVerificationResult,
    ) -> None:
        if summary_key not in summary:
            return
        value = summary.get(summary_key)
        if not isinstance(value, dict):
            result.errors.append(f"summary.{summary_key} must be an object")
            return
        actual_counts: dict[str, int] = {}
        for status, raw_count in value.items():
            parsed = self._as_int(raw_count)
            if parsed is None:
                result.errors.append(f"summary.{summary_key}.{status} must be an integer")
                continue
            actual_counts[str(status)] = parsed
        if actual_counts != expected_counts:
            result.errors.append(
                f"summary.{summary_key}={actual_counts} does not match actual status counts={expected_counts}"
            )

    def _verify_summary_failure_counts(
        self,
        summary: dict[str, Any],
        workitems: list[Any],
        result: ManifestVerificationResult,
    ) -> None:
        failed_items = [
            item
            for item in workitems
            if isinstance(item, dict) and str(item.get("status", "")) == "failed"
        ]
        failed_ids = [str(item.get("id", "")) for item in failed_items if str(item.get("id", ""))]
        if "failed_workitem_ids" in summary:
            if not isinstance(summary.get("failed_workitem_ids"), list):
                result.errors.append("summary.failed_workitem_ids must be a list")
            else:
                actual_ids = self._string_list(summary.get("failed_workitem_ids"))
                if actual_ids != failed_ids:
                    result.errors.append(
                        f"summary.failed_workitem_ids={actual_ids} does not match failed WorkItems={failed_ids}"
                    )
        expected_retryable = len([item for item in failed_items if item.get("retryable") is True])
        expected_non_retryable = len([item for item in failed_items if item.get("retryable") is not True])
        for summary_key, expected in (
            ("retryable_failure_count", expected_retryable),
            ("non_retryable_failure_count", expected_non_retryable),
        ):
            if summary_key not in summary:
                continue
            actual = self._as_int(summary.get(summary_key))
            if actual is None:
                result.errors.append(f"summary.{summary_key} must be an integer")
            elif actual != expected:
                result.errors.append(f"summary.{summary_key}={actual} does not match failed WorkItems={expected}")

    def _verify_summary_retry_attempt_count(
        self,
        summary: dict[str, Any],
        retry_history: list[Any],
        result: ManifestVerificationResult,
    ) -> None:
        if "retry_attempt_count" not in summary:
            return
        actual = self._as_int(summary.get("retry_attempt_count"))
        if actual is None:
            result.errors.append("summary.retry_attempt_count must be an integer")
            return
        expected = 0
        for retry_record in retry_history:
            if not isinstance(retry_record, dict):
                continue
            parsed = self._as_int(retry_record.get("retry_count", 0))
            if parsed is not None:
                expected += parsed
        if actual != expected:
            result.errors.append(f"summary.retry_attempt_count={actual} does not match retry_history total={expected}")

    def _verify_requirement_quality_score(
        self,
        summary: dict[str, Any],
        requirement_evaluations: list[Any],
        result: ManifestVerificationResult,
    ) -> None:
        if "requirement_quality_score" not in summary:
            return
        actual = self._as_int(summary.get("requirement_quality_score"))
        if actual is None:
            result.errors.append("summary.requirement_quality_score must be an integer")
            return
        scores = [
            parsed
            for evaluation in requirement_evaluations
            if isinstance(evaluation, dict)
            for parsed in [self._as_int(evaluation.get("score"))]
            if parsed is not None
        ]
        expected = max(scores, default=0)
        if actual != expected:
            result.errors.append(
                f"summary.requirement_quality_score={actual} does not match max(requirement_evaluations.score)={expected}"
            )

    def _verify_requirement_coverage_status(
        self,
        summary: dict[str, Any],
        coverage_results: list[Any],
        result: ManifestVerificationResult,
    ) -> None:
        if "requirement_coverage_status" not in summary:
            return
        expected = self._requirement_coverage_status(coverage_results)
        actual = str(summary.get("requirement_coverage_status", ""))
        if actual != expected:
            result.errors.append(
                f"summary.requirement_coverage_status={actual} does not match requirement coverage results={expected}"
            )

    def _requirement_coverage_status(self, coverage_results: list[Any]) -> str:
        records = [item for item in coverage_results if isinstance(item, dict)]
        if not records:
            return "not_evaluated"
        if any(record.get("passed") is False for record in records):
            return "missing_coverage"
        if any(bool(self._string_list(record.get("required_rules", []))) for record in records):
            return "pass"
        return "no_rules"

    def _verify_scope_contract_summary(
        self,
        summary: dict[str, Any],
        scope_results: list[Any],
        result: ManifestVerificationResult,
    ) -> None:
        if "scope_contract_status" in summary:
            expected_status = self._scope_contract_status(scope_results)
            actual_status = str(summary.get("scope_contract_status", ""))
            if actual_status != expected_status:
                result.errors.append(
                    f"summary.scope_contract_status={actual_status} does not match scope contract results={expected_status}"
                )
        if "scope_contract_violation_count" in summary:
            actual_count = self._as_int(summary.get("scope_contract_violation_count"))
            if actual_count is None:
                result.errors.append("summary.scope_contract_violation_count must be an integer")
                return
            expected_count = sum(
                len(self._list(record.get("violations", [])))
                for record in scope_results
                if isinstance(record, dict)
            )
            if actual_count != expected_count:
                result.errors.append(
                    f"summary.scope_contract_violation_count={actual_count} "
                    f"does not match scope contract violations={expected_count}"
                )

    def _scope_contract_status(self, scope_results: list[Any]) -> str:
        records = [item for item in scope_results if isinstance(item, dict)]
        if not records:
            return "not_evaluated"
        if any(record.get("passed") is False for record in records):
            return "violation"
        if any(bool(self._string_list(record.get("rule_ids", []))) for record in records):
            return "pass"
        return "no_rules"

    def _verify_llm_context_windows(
        self,
        summary: dict[str, Any],
        llm_runs: list[Any],
        result: ManifestVerificationResult,
    ) -> None:
        if "llm_context_windows" not in summary:
            return
        context_windows = summary.get("llm_context_windows")
        if not isinstance(context_windows, list):
            result.errors.append("summary.llm_context_windows must be a list")
            return
        context_by_model: dict[str, int | None] = {}
        for index, record in enumerate(context_windows):
            if not isinstance(record, dict):
                result.errors.append(f"summary.llm_context_windows[{index}] must be an object")
                continue
            model = str(record.get("model", ""))
            if not model:
                continue
            context_length = record.get("context_length")
            if context_length is not None and not isinstance(context_length, int):
                result.errors.append(f"summary.llm_context_windows[{index}].context_length must be an integer or null")
                continue
            if model in context_by_model and context_by_model[model] != context_length:
                result.errors.append(f"summary.llm_context_windows contains conflicting context_length for model: {model}")
            context_by_model[model] = context_length

        for index, run in enumerate(llm_runs):
            if not isinstance(run, dict):
                continue
            model = str(run.get("model", ""))
            if not model or model not in context_by_model:
                continue
            expected = context_by_model[model]
            actual = run.get("context_length")
            if actual != expected:
                result.errors.append(
                    f"llm_runs[{index}].context_length={actual} does not match summary.llm_context_windows[{model}]={expected}"
                )

    def _verify_task_center_summary(
        self,
        summary: dict[str, Any],
        task_assignments: list[Any],
        result: ManifestVerificationResult,
    ) -> None:
        if "task_center_summary" not in summary:
            return
        task_summary = summary.get("task_center_summary")
        if not isinstance(task_summary, dict):
            result.errors.append("summary.task_center_summary must be an object")
            return
        expected_counts = {
            "total": len([item for item in task_assignments if isinstance(item, dict)]),
            "claimable": len([item for item in task_assignments if isinstance(item, dict) and item.get("claimable") is True]),
            "blocked_by_dependencies": len(
                [
                    item
                    for item in task_assignments
                    if isinstance(item, dict)
                    and str(item.get("status", "")) == "queued"
                    and bool(self._string_list(item.get("unmet_dependency_ids", [])))
                ]
            ),
            "stale_claimed": len([item for item in task_assignments if isinstance(item, dict) and item.get("stale_claimed") is True]),
        }
        for assignment in task_assignments:
            if not isinstance(assignment, dict):
                continue
            status = str(assignment.get("status", ""))
            if status:
                expected_counts[status] = expected_counts.get(status, 0) + 1
        for summary_key, expected in expected_counts.items():
            if summary_key not in task_summary:
                continue
            actual = self._as_int(task_summary.get(summary_key))
            if actual is None:
                result.errors.append(f"summary.task_center_summary.{summary_key} must be an integer")
            elif actual != expected:
                result.errors.append(
                    f"summary.task_center_summary.{summary_key}={actual} does not match task_assignments={expected}"
                )

    def _verify_summary_blockers(
        self,
        summary: dict[str, Any],
        cursor: dict[str, Any],
        result: ManifestVerificationResult,
    ) -> None:
        blocked_reasons: list[str] = []
        if "blocked_reasons" in summary:
            if not isinstance(summary.get("blocked_reasons"), list):
                result.errors.append("summary.blocked_reasons must be a list")
            else:
                blocked_reasons = self._string_list(summary.get("blocked_reasons"))
        if "blocked_count" in summary:
            actual = self._as_int(summary.get("blocked_count"))
            if actual is None:
                result.errors.append("summary.blocked_count must be an integer")
            elif actual != len(blocked_reasons):
                result.errors.append(
                    f"summary.blocked_count={actual} does not match len(summary.blocked_reasons)={len(blocked_reasons)}"
                )
        if blocked_reasons and isinstance(cursor.get("blockers"), list):
            cursor_blockers = self._string_list(cursor.get("blockers"))
            if blocked_reasons != cursor_blockers:
                result.errors.append(
                    f"summary.blocked_reasons={blocked_reasons} does not match resume_cursor.blockers={cursor_blockers}"
                )

    def _verify_summary_validation_failure_count(
        self,
        summary: dict[str, Any],
        executions: list[Any],
        result: ManifestVerificationResult,
    ) -> None:
        if "validation_failure_count" not in summary:
            return
        actual = self._as_int(summary.get("validation_failure_count"))
        if actual is None:
            result.errors.append("summary.validation_failure_count must be an integer")
            return
        expected = len(
            [
                execution
                for execution in executions
                if isinstance(execution, dict) and execution.get("validation_success") is False
            ]
        )
        if actual != expected:
            result.errors.append(
                f"summary.validation_failure_count={actual} does not match failed validations={expected}"
            )

    def _verify_resume_cursor(self, payload: dict[str, Any], result: ManifestVerificationResult) -> None:
        cursor = self._dict(payload.get("resume_cursor"))
        if not cursor:
            return
        project_id = str(payload.get("project_id", ""))
        if str(cursor.get("project_id", "")) != project_id:
            result.errors.append("resume_cursor.project_id does not match manifest.project_id")
        final_status = str(payload.get("final_status", payload.get("status", "")))
        if str(cursor.get("project_status", "")) != final_status:
            result.errors.append("resume_cursor.project_status does not match manifest.final_status")
        if str(cursor.get("current_stage", "")) != str(payload.get("current_stage", "")):
            result.errors.append("resume_cursor.current_stage does not match manifest.current_stage")

        next_action = str(cursor.get("next_action", ""))
        if final_status == "completed" and next_action != "complete":
            result.errors.append("completed manifest must have resume_cursor.next_action=complete")
        if final_status == "blocked" and next_action != "blocked":
            result.errors.append("blocked manifest must have resume_cursor.next_action=blocked")
        if final_status in {"completed", "blocked"} and cursor.get("terminal") is not True:
            result.errors.append("terminal manifest must have resume_cursor.terminal=true")
        if final_status not in {"completed", "blocked"} and cursor.get("terminal") is True:
            result.errors.append("non-terminal manifest cannot have resume_cursor.terminal=true")
        if final_status == "blocked" and cursor.get("blocked") is not True:
            result.errors.append("blocked manifest must have resume_cursor.blocked=true")
        if final_status != "blocked" and cursor.get("blocked") is True:
            result.errors.append("non-blocked manifest cannot have resume_cursor.blocked=true")
        if "blockers" in cursor and not isinstance(cursor.get("blockers"), list):
            result.warnings.append("resume_cursor.blockers must be a list")

        workitem_ids = self._id_set(self._list(payload.get("workitems")))
        workitem_statuses = {
            str(item.get("id", "")): str(item.get("status", ""))
            for item in self._list(payload.get("workitems"))
            if isinstance(item, dict) and str(item.get("id", ""))
        }
        for cursor_key in self.CURSOR_WORKITEM_LISTS:
            if cursor_key in cursor and not isinstance(cursor.get(cursor_key), list):
                result.warnings.append(f"resume_cursor.{cursor_key} must be a list")
            for workitem_id in self._string_list(cursor.get(cursor_key)):
                if workitem_id not in workitem_ids:
                    result.errors.append(f"resume_cursor.{cursor_key} references unknown WorkItem: {workitem_id}")
        self._verify_cursor_workitem_statuses(cursor, workitem_statuses, result)
        if final_status == "completed":
            for cursor_key in (
                "next_pending_workitem_ids",
                "running_workitem_ids",
                "retryable_failed_workitem_ids",
                "terminal_failed_workitem_ids",
            ):
                if self._string_list(cursor.get(cursor_key)):
                    result.errors.append(f"completed manifest cannot have resume_cursor.{cursor_key}")
            if self._string_list(cursor.get("blockers")):
                result.errors.append("completed manifest cannot have resume_cursor.blockers")
        last_execution_workitem_id = str(cursor.get("last_execution_workitem_id", ""))
        if last_execution_workitem_id and last_execution_workitem_id not in workitem_ids:
            result.errors.append(
                f"resume_cursor.last_execution_workitem_id references unknown WorkItem: {last_execution_workitem_id}"
            )

    def _verify_cursor_workitem_statuses(
        self,
        cursor: dict[str, Any],
        workitem_statuses: dict[str, str],
        result: ManifestVerificationResult,
    ) -> None:
        expected_statuses = {
            "next_pending_workitem_ids": "pending",
            "running_workitem_ids": "running",
            "retryable_failed_workitem_ids": "failed",
            "terminal_failed_workitem_ids": "failed",
            "completed_workitem_ids": "done",
        }
        for cursor_key, expected_status in expected_statuses.items():
            for workitem_id in self._string_list(cursor.get(cursor_key)):
                actual_status = workitem_statuses.get(workitem_id)
                if actual_status and actual_status != expected_status:
                    result.errors.append(
                        f"resume_cursor.{cursor_key} references WorkItem {workitem_id} "
                        f"with status {actual_status}, expected {expected_status}"
                    )

    def _verify_links(self, payload: dict[str, Any], result: ManifestVerificationResult) -> None:
        project_id = str(payload.get("project_id", ""))
        workitems = self._list(payload.get("workitems"))
        artifacts = self._list(payload.get("artifacts"))
        executions = self._list(payload.get("executions"))
        task_assignments = self._list(payload.get("task_assignments"))
        retry_history = self._list(payload.get("retry_history"))
        collaboration_runs = self._list(payload.get("collaboration_runs"))
        agent_ids = self._agent_ids(payload, result)

        workitem_ids = self._ids_with_duplicate_check("workitems", workitems, result)
        artifact_ids = self._ids_with_duplicate_check("artifacts", artifacts, result)
        workitem_statuses = {
            str(item.get("id", "")): str(item.get("status", ""))
            for item in workitems
            if isinstance(item, dict) and str(item.get("id", ""))
        }

        for index, agent in enumerate(self._list(payload.get("agents"))):
            if not isinstance(agent, dict):
                continue
            self._warn_non_list_fields(
                agent,
                f"agents[{index}]",
                ("workitem_ids", "artifact_ids", "output_files"),
                result,
            )
            for workitem_id in self._string_list(agent.get("workitem_ids", [])):
                if workitem_id not in workitem_ids:
                    result.warnings.append(f"agents[{index}] workitem_ids references unknown WorkItem: {workitem_id}")
            for artifact_id in self._string_list(agent.get("artifact_ids", [])):
                if artifact_id not in artifact_ids:
                    result.warnings.append(f"agents[{index}] artifact_ids references unknown Artifact: {artifact_id}")

        for workitem in workitems:
            if not isinstance(workitem, dict):
                continue
            workitem_id = str(workitem.get("id", ""))
            self._warn_non_list_fields(
                workitem,
                f"workitem {workitem_id}",
                ("dependencies", "input_artifact_ids", "output_artifact_ids"),
                result,
            )
            for dependency_id in self._string_list(workitem.get("dependencies", [])):
                if dependency_id not in workitem_ids:
                    result.errors.append(f"workitem {workitem_id} dependency references unknown WorkItem: {dependency_id}")
            for artifact_id in self._string_list(workitem.get("input_artifact_ids", [])):
                if artifact_id not in artifact_ids:
                    result.errors.append(
                        f"workitem {workitem_id} input_artifact_ids references unknown Artifact: {artifact_id}"
                    )
            for artifact_id in self._string_list(workitem.get("output_artifact_ids", [])):
                if artifact_id not in artifact_ids:
                    result.warnings.append(
                        f"workitem {workitem_id} output_artifact_ids is not indexed in artifacts: {artifact_id}"
                    )

        for execution in executions:
            workitem_id = str(execution.get("workitem_id", "")) if isinstance(execution, dict) else ""
            if isinstance(execution, dict):
                self._warn_non_list_fields(
                    execution,
                    f"execution for {workitem_id}",
                    ("artifact_ids", "artifact_files", "changed_files"),
                    result,
                )
            if workitem_id and workitem_id not in workitem_ids:
                result.errors.append(f"execution references unknown WorkItem: {workitem_id}")
            self._warn_unknown_agent(agent_ids, str(execution.get("agent_id", "")) if isinstance(execution, dict) else "", f"execution for {workitem_id}", result)
            for artifact_id in self._string_list(execution.get("artifact_ids", []) if isinstance(execution, dict) else []):
                if artifact_id not in artifact_ids:
                    result.errors.append(f"execution references unknown Artifact: {artifact_id}")
        self._verify_latest_execution_workitem_status(executions, workitem_statuses, result)

        for artifact in artifacts:
            if not isinstance(artifact, dict):
                continue
            artifact_id = str(artifact.get("id", ""))
            artifact_workitem_id = str(artifact.get("workitem_id", ""))
            if artifact_workitem_id and artifact_workitem_id not in workitem_ids:
                result.errors.append(f"artifact references unknown WorkItem: {artifact_workitem_id}")
            self._warn_unknown_agent(agent_ids, str(artifact.get("agent_id", "")), f"artifact {artifact_id}", result)
            artifact_project_id = str(artifact.get("project_id", ""))
            if artifact_project_id and artifact_project_id != project_id:
                result.errors.append(f"artifact.project_id does not match manifest.project_id: {artifact.get('id', '')}")
            for field_name in ("parent_artifact_id", "review_of"):
                linked_id = str(artifact.get(field_name, ""))
                if linked_id and linked_id == artifact_id:
                    result.warnings.append(f"artifact {artifact_id} has self-referential {field_name}")
                elif linked_id and linked_id not in artifact_ids:
                    result.warnings.append(f"artifact {artifact.get('id', '')} has unresolved {field_name}: {linked_id}")
            if "derived_from" in artifact and not isinstance(artifact.get("derived_from"), list):
                result.warnings.append(f"artifact {artifact_id} derived_from must be a list")
            for linked_id in self._string_list(artifact.get("derived_from", [])):
                if linked_id == artifact_id:
                    result.warnings.append(f"artifact {artifact_id} has self-referential derived_from")
                elif linked_id not in artifact_ids:
                    result.warnings.append(f"artifact {artifact.get('id', '')} has unresolved derived_from: {linked_id}")

        for assignment in task_assignments:
            if not isinstance(assignment, dict):
                continue
            assignment_id = str(assignment.get("id", ""))
            workitem_id = str(assignment.get("workitem_id", ""))
            self._warn_unknown_agent(agent_ids, str(assignment.get("assigned_agent_id", "")), f"task assignment {assignment_id}", result)
            self._warn_non_list_fields(
                assignment,
                f"task assignment {assignment_id}",
                ("dependencies", "input_artifact_ids", "output_artifact_ids"),
                result,
            )
            if workitem_id and workitem_id not in workitem_ids:
                result.errors.append(f"task assignment references unknown WorkItem: {workitem_id}")
            self._verify_task_assignment_workitem_status(assignment, workitem_statuses, result)
            for dependency_id in self._string_list(assignment.get("dependencies", [])):
                if dependency_id not in workitem_ids:
                    result.errors.append(
                        f"task assignment {assignment_id} dependency references unknown WorkItem: {dependency_id}"
                    )
            for artifact_id in self._string_list(assignment.get("input_artifact_ids", [])):
                if artifact_id not in artifact_ids:
                    result.errors.append(
                        f"task assignment {assignment_id} input_artifact_ids references unknown Artifact: {artifact_id}"
                    )
            for artifact_id in self._string_list(assignment.get("output_artifact_ids", [])):
                if artifact_id not in artifact_ids:
                    result.warnings.append(
                        f"task assignment {assignment_id} output_artifact_ids is not indexed in artifacts: {artifact_id}"
                    )

        for run_list_name in ("cli_runs", "llm_runs"):
            for index, run in enumerate(self._list(payload.get(run_list_name))):
                if not isinstance(run, dict):
                    continue
                self._warn_non_list_fields(run, f"{run_list_name}[{index}]", ("output_files",), result)
                if run_list_name == "llm_runs" and "token_usage" in run:
                    self._verify_token_usage(run.get("token_usage"), f"llm_runs[{index}].token_usage", result)

        for index, retry_record in enumerate(retry_history):
            if not isinstance(retry_record, dict):
                continue
            workitem_id = str(retry_record.get("workitem_id", ""))
            if workitem_id and workitem_id not in workitem_ids:
                result.errors.append(f"retry_history[{index}] references unknown WorkItem: {workitem_id}")
            self._warn_non_list_fields(
                retry_record,
                f"retry_history[{index}]",
                ("related_events", "related_gate_history"),
                result,
            )
            for field_name in ("retry_count", "max_retries"):
                if field_name in retry_record:
                    parsed = self._as_int(retry_record.get(field_name))
                    if parsed is None:
                        result.warnings.append(f"retry_history[{index}].{field_name} must be an integer")
                    elif parsed < 0:
                        result.warnings.append(f"retry_history[{index}].{field_name} must be non-negative")

        for index, collaboration in enumerate(collaboration_runs):
            if not isinstance(collaboration, dict):
                continue
            collaboration_id = str(collaboration.get("id", ""))
            workitem_id = str(collaboration.get("workitem_id", ""))
            if workitem_id and workitem_id not in workitem_ids:
                result.errors.append(f"collaboration_runs[{index}] references unknown WorkItem: {workitem_id}")
            final_artifact_id = str(collaboration.get("final_artifact_id", ""))
            if final_artifact_id and final_artifact_id not in artifact_ids:
                result.warnings.append(
                    f"collaboration_runs[{index}] final_artifact_id is not indexed in artifacts: {final_artifact_id}"
                )
            self._warn_unknown_agent(
                agent_ids,
                str(collaboration.get("lead_agent_id", "")),
                f"collaboration_runs[{index}].lead_agent_id",
                result,
            )
            for reviewer_agent_id in self._string_list(collaboration.get("reviewer_agent_ids", [])):
                self._warn_unknown_agent(
                    agent_ids,
                    reviewer_agent_id,
                    f"collaboration_runs[{index}].reviewer_agent_ids",
                    result,
                )
            reviews = self._list(collaboration.get("reviews", []))
            draft_versions = self._list(collaboration.get("draft_versions", []))
            review_ids = {str(review.get("id", "")) for review in reviews if isinstance(review, dict) and review.get("id")}
            self._warn_non_list_fields(collaboration, f"collaboration_runs[{index}]", ("reviewer_agent_ids", "reviews", "draft_versions"), result)
            if "review_count" in collaboration and self._as_int(collaboration.get("review_count")) != len(reviews):
                result.warnings.append(f"collaboration_runs[{index}].review_count does not match len(reviews)")
            if "draft_version_count" in collaboration and self._as_int(collaboration.get("draft_version_count")) != len(draft_versions):
                result.warnings.append(f"collaboration_runs[{index}].draft_version_count does not match len(draft_versions)")
            for draft_index, draft in enumerate(draft_versions):
                if not isinstance(draft, dict):
                    continue
                self._warn_unknown_agent(
                    agent_ids,
                    str(draft.get("author_agent_id", "")),
                    f"collaboration_runs[{index}].draft_versions[{draft_index}].author_agent_id",
                    result,
                )
                self._warn_non_list_fields(
                    draft,
                    f"collaboration_runs[{index}].draft_versions[{draft_index}]",
                    ("review_ids",),
                    result,
                )
                for review_id in self._string_list(draft.get("review_ids", [])):
                    if review_id not in review_ids:
                        result.warnings.append(
                            f"collaboration_runs[{index}].draft_versions[{draft_index}] references unknown review_id: {review_id}"
                        )
            for review_index, review in enumerate(reviews):
                if not isinstance(review, dict):
                    continue
                self._warn_unknown_agent(
                    agent_ids,
                    str(review.get("agent_id", "")),
                    f"collaboration_runs[{index}].reviews[{review_index}].agent_id",
                    result,
                )

    def _verify_latest_execution_workitem_status(
        self,
        executions: list[Any],
        workitem_statuses: dict[str, str],
        result: ManifestVerificationResult,
    ) -> None:
        latest_by_workitem: dict[str, dict[str, Any]] = {}
        for execution in executions:
            if not isinstance(execution, dict):
                continue
            workitem_id = str(execution.get("workitem_id", ""))
            if workitem_id:
                latest_by_workitem[workitem_id] = execution

        expected_workitem_statuses = {
            "success": "done",
            "failed": "failed",
        }
        for workitem_id, execution in latest_by_workitem.items():
            execution_status = str(execution.get("status", ""))
            expected_status = expected_workitem_statuses.get(execution_status)
            actual_status = workitem_statuses.get(workitem_id)
            if expected_status and actual_status and actual_status != expected_status:
                result.warnings.append(
                    f"latest execution for WorkItem {workitem_id} has status {execution_status}, "
                    f"but WorkItem status is {actual_status}, expected {expected_status}"
                )

    def _verify_task_assignment_workitem_status(
        self,
        assignment: dict[str, Any],
        workitem_statuses: dict[str, str],
        result: ManifestVerificationResult,
    ) -> None:
        assignment_id = str(assignment.get("id", ""))
        workitem_id = str(assignment.get("workitem_id", ""))
        assignment_status = str(assignment.get("status", ""))
        expected_workitem_statuses = {
            "queued": "pending",
            "claimed": "running",
            "blocked": "failed",
        }
        expected_status = expected_workitem_statuses.get(assignment_status)
        actual_status = workitem_statuses.get(workitem_id)
        if expected_status and actual_status and actual_status != expected_status:
            result.warnings.append(
                f"task assignment {assignment_id} status {assignment_status} references WorkItem {workitem_id} "
                f"with status {actual_status}, expected {expected_status}"
            )

    def _warn_non_list_fields(
        self,
        item: dict[str, Any],
        owner: str,
        field_names: tuple[str, ...],
        result: ManifestVerificationResult,
    ) -> None:
        """Warn when relationship fields are present but not encoded as lists."""
        for field_name in field_names:
            if field_name in item and not isinstance(item.get(field_name), list):
                result.warnings.append(f"{owner} {field_name} must be a list")

    def _verify_token_usage(self, value: Any, owner: str, result: ManifestVerificationResult) -> None:
        """Warn when token usage cannot be safely aggregated."""
        if not isinstance(value, dict):
            result.warnings.append(f"{owner} must be an object")
            return
        for key, token_count in value.items():
            parsed = self._as_int(token_count)
            if parsed is None:
                result.warnings.append(f"{owner}.{key} must be an integer")
            elif parsed < 0:
                result.warnings.append(f"{owner}.{key} must be non-negative")

    def _verify_llm_cost_estimate(self, value: Any, owner: str, result: ManifestVerificationResult) -> None:
        """Warn when cost estimate fields cannot be safely audited."""
        if not isinstance(value, dict):
            result.warnings.append(f"{owner} must be an object")
            return
        estimated_total = self._as_float(value.get("estimated_total"))
        if "estimated_total" in value and estimated_total is None:
            result.warnings.append(f"{owner}.estimated_total must be a number")
        elif estimated_total is not None and estimated_total < 0:
            result.warnings.append(f"{owner}.estimated_total must be non-negative")
        model_costs = value.get("model_costs", [])
        if "model_costs" in value and not isinstance(model_costs, list):
            result.warnings.append(f"{owner}.model_costs must be a list")
            return
        for index, item in enumerate(model_costs if isinstance(model_costs, list) else []):
            if not isinstance(item, dict):
                result.warnings.append(f"{owner}.model_costs[{index}] must be an object")
                continue
            estimated_cost = self._as_float(item.get("estimated_cost"))
            if "estimated_cost" in item and estimated_cost is None:
                result.warnings.append(f"{owner}.model_costs[{index}].estimated_cost must be a number")
            elif estimated_cost is not None and estimated_cost < 0:
                result.warnings.append(f"{owner}.model_costs[{index}].estimated_cost must be non-negative")

    def _verify_no_secret_leaks(self, payload: dict[str, Any], result: ManifestVerificationResult) -> None:
        """Fail manifests that appear to contain API credentials.

        `task_assignments[].claim_token` is currently part of the manifest
        contract, so this check deliberately targets provider/API credentials
        rather than every field with "token" in the name.
        """
        for path, value in self._walk_json(payload):
            key = path[-1] if path else ""
            if key.lower() in self.SECRET_FIELD_NAMES and str(value or ""):
                result.errors.append(f"manifest contains sensitive field: {'.'.join(path)}")
                continue
            if isinstance(value, str) and self._looks_like_secret_value(value):
                result.errors.append(f"manifest contains sensitive-looking value at: {'.'.join(path)}")

    def _walk_json(self, value: Any, path: tuple[str, ...] = ()) -> list[tuple[tuple[str, ...], Any]]:
        if isinstance(value, dict):
            items: list[tuple[tuple[str, ...], Any]] = []
            for key, child in value.items():
                items.extend(self._walk_json(child, (*path, str(key))))
            return items
        if isinstance(value, list):
            items = []
            for index, child in enumerate(value):
                items.extend(self._walk_json(child, (*path, str(index))))
            return items
        return [(path, value)]

    def _looks_like_secret_value(self, value: str) -> bool:
        return any(pattern.search(value) for pattern in self.SECRET_VALUE_PATTERNS)

    def _verify_files(self, manifest_path: Path, payload: dict[str, Any], result: ManifestVerificationResult) -> None:
        project_root = Path(str(payload.get("project_root", ""))) if str(payload.get("project_root", "")) else None
        files = self._dict(payload.get("files"))
        self._warn_non_list_fields(files, "files", ("artifacts", "task_prompts"), result)
        for label in ("log", "report", "preflight_gate"):
            raw_path = str(files.get(label, ""))
            if raw_path and not self._path_exists(raw_path, manifest_path, project_root):
                result.warnings.append(f"files.{label} does not exist: {raw_path}")
        self._verify_preflight_gate_file(manifest_path, project_root, payload, files, result)

        indexed_manifest_path = str(files.get("manifest", ""))
        if indexed_manifest_path and not self._same_path(indexed_manifest_path, manifest_path):
            result.warnings.append(f"files.manifest differs from verified path: {indexed_manifest_path}")

        for field_name in ("artifact_files", "task_prompt_files"):
            for raw_path in self._string_list(payload.get(field_name, [])):
                if raw_path and not self._path_exists(raw_path, manifest_path, project_root):
                    result.warnings.append(f"{field_name} entry does not exist: {raw_path}")
        for files_key, top_level_key in (("artifacts", "artifact_files"), ("task_prompts", "task_prompt_files")):
            top_level_paths = set(self._string_list(payload.get(top_level_key, [])))
            files_paths = set(self._string_list(files.get(files_key, [])))
            for raw_path in self._string_list(files.get(files_key, [])):
                if raw_path and not self._path_exists(raw_path, manifest_path, project_root):
                    result.warnings.append(f"files.{files_key} entry does not exist: {raw_path}")
                if raw_path and raw_path not in top_level_paths:
                    result.warnings.append(f"files.{files_key} entry is not indexed in {top_level_key}: {raw_path}")
            for raw_path in top_level_paths:
                if raw_path and raw_path not in files_paths:
                    result.warnings.append(f"{top_level_key} entry is not indexed in files.{files_key}: {raw_path}")

        artifact_files = set(self._string_list(payload.get("artifact_files", [])))
        for artifact in self._list(payload.get("artifacts")):
            if not isinstance(artifact, dict):
                continue
            artifact_path = str(artifact.get("path", ""))
            if artifact_path and artifact_path not in artifact_files:
                result.warnings.append(f"artifact path is not indexed in artifact_files: {artifact_path}")

        for execution in self._list(payload.get("executions")):
            if not isinstance(execution, dict):
                continue
            workitem_id = str(execution.get("workitem_id", ""))
            for raw_path in self._string_list(execution.get("artifact_files", [])):
                if raw_path and not self._path_exists(raw_path, manifest_path, project_root):
                    result.warnings.append(f"execution {workitem_id} artifact_files entry does not exist: {raw_path}")
                if raw_path and raw_path not in artifact_files:
                    result.warnings.append(f"execution {workitem_id} artifact_files entry is not indexed in artifact_files: {raw_path}")

        task_prompt_files = set(self._string_list(payload.get("task_prompt_files", [])))
        for assignment in self._list(payload.get("task_assignments")):
            if not isinstance(assignment, dict):
                continue
            prompt_file = str(assignment.get("prompt_file", ""))
            if prompt_file and not self._path_exists(prompt_file, manifest_path, project_root):
                result.warnings.append(f"task assignment prompt_file does not exist: {prompt_file}")
            if prompt_file and prompt_file not in task_prompt_files:
                result.warnings.append(f"task assignment prompt_file is not indexed in task_prompt_files: {prompt_file}")

        for run_list_name in ("cli_runs", "llm_runs"):
            for index, run in enumerate(self._list(payload.get(run_list_name))):
                if not isinstance(run, dict):
                    continue
                for raw_path in self._string_list(run.get("output_files", [])):
                    if raw_path and not self._path_exists(raw_path, manifest_path, project_root):
                        result.warnings.append(f"{run_list_name}[{index}].output_files entry does not exist: {raw_path}")

        for index, collaboration in enumerate(self._list(payload.get("collaboration_runs"))):
            if not isinstance(collaboration, dict):
                continue
            for section_name in ("reviews", "draft_versions"):
                for item_index, item in enumerate(self._list(collaboration.get(section_name, []))):
                    if not isinstance(item, dict):
                        continue
                    output_path = str(item.get("output_path", ""))
                    if output_path and not self._path_exists(output_path, manifest_path, project_root):
                        result.warnings.append(
                            f"collaboration_runs[{index}].{section_name}[{item_index}].output_path does not exist: {output_path}"
                        )

        for index, agent in enumerate(self._list(payload.get("agents"))):
            if not isinstance(agent, dict):
                continue
            for raw_path in self._string_list(agent.get("output_files", [])):
                if raw_path and not self._path_exists(raw_path, manifest_path, project_root):
                    result.warnings.append(f"agents[{index}].output_files entry does not exist: {raw_path}")

    def _verify_preflight_gate_file(
        self,
        manifest_path: Path,
        project_root: Path | None,
        payload: dict[str, Any],
        files: dict[str, Any],
        result: ManifestVerificationResult,
    ) -> None:
        raw_path = str(files.get("preflight_gate", ""))
        if not raw_path:
            return
        gate_path = self._resolve_existing_path(raw_path, manifest_path, project_root)
        if gate_path is None:
            return
        try:
            gate_payload = json.loads(gate_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as error:
            result.warnings.append(f"files.preflight_gate is not readable JSON: {error}")
            return
        if not isinstance(gate_payload, dict):
            result.errors.append("files.preflight_gate payload must be an object")
            return
        gate = gate_payload.get("preflight_gate", {})
        if not isinstance(gate, dict):
            gate = {}
        expected_ok = gate_payload.get("ok") if isinstance(gate_payload.get("ok"), bool) else None
        expected_errors = self._string_list(gate.get("errors", []))
        expected_recommendations = self._string_list(gate.get("recommendations", []))
        summary = self._dict(payload.get("summary"))
        if "preflight_gate_ok" in summary and summary.get("preflight_gate_ok") != expected_ok:
            result.errors.append(
                f"summary.preflight_gate_ok={summary.get('preflight_gate_ok')} does not match preflight gate ok={expected_ok}"
            )
        if "preflight_gate_errors" in summary and self._string_list(summary.get("preflight_gate_errors")) != expected_errors:
            result.errors.append(
                f"summary.preflight_gate_errors={self._string_list(summary.get('preflight_gate_errors'))} "
                f"does not match preflight gate errors={expected_errors}"
            )
        if (
            "preflight_gate_recommendations" in summary
            and self._string_list(summary.get("preflight_gate_recommendations")) != expected_recommendations
        ):
            result.errors.append(
                f"summary.preflight_gate_recommendations={self._string_list(summary.get('preflight_gate_recommendations'))} "
                f"does not match preflight gate recommendations={expected_recommendations}"
            )

    def _ids_with_duplicate_check(
        self,
        label: str,
        records: list[Any],
        result: ManifestVerificationResult,
    ) -> set[str]:
        ids: set[str] = set()
        duplicates: set[str] = set()
        for record in records:
            if not isinstance(record, dict):
                result.errors.append(f"{label} contains a non-object record")
                continue
            record_id = str(record.get("id", ""))
            if not record_id:
                result.errors.append(f"{label} contains a record without id")
                continue
            if record_id in ids:
                duplicates.add(record_id)
            ids.add(record_id)
        for record_id in sorted(duplicates):
            result.errors.append(f"{label} contains duplicate id: {record_id}")
        return ids

    def _agent_ids(self, payload: dict[str, Any], result: ManifestVerificationResult) -> set[str]:
        """Return indexed Agent ids from manifest agents records."""
        ids: set[str] = set()
        duplicates: set[str] = set()
        for agent in self._list(payload.get("agents")):
            if not isinstance(agent, dict):
                result.errors.append("agents contains a non-object record")
                continue
            agent_id = str(agent.get("agent_id", ""))
            if not agent_id:
                result.errors.append("agents contains a record without agent_id")
                continue
            if agent_id in ids:
                duplicates.add(agent_id)
            ids.add(agent_id)
        for agent_id in sorted(duplicates):
            result.errors.append(f"agents contains duplicate agent_id: {agent_id}")
        return ids

    def _warn_unknown_agent(
        self,
        agent_ids: set[str],
        agent_id: str,
        owner: str,
        result: ManifestVerificationResult,
    ) -> None:
        """Warn on dangling Agent references when the manifest has an Agent index."""
        if agent_ids and agent_id and not self._agent_reference_known(agent_ids, agent_id):
            result.warnings.append(f"{owner} references unknown Agent: {agent_id}")

    def _agent_reference_known(self, agent_ids: set[str], agent_id: str) -> bool:
        """Return whether an Agent id or specialized review seat is indexed."""
        if agent_id in agent_ids:
            return True
        base_agent_id, separator, _ = agent_id.partition(":")
        return bool(separator and base_agent_id in agent_ids)

    def _id_set(self, records: list[Any]) -> set[str]:
        return {str(record.get("id", "")) for record in records if isinstance(record, dict) and record.get("id")}

    def _path_exists(self, raw_path: str, manifest_path: Path, project_root: Path | None) -> bool:
        path = Path(raw_path)
        candidates = [path] if path.is_absolute() else []
        if project_root is not None:
            candidates.append(project_root / path)
        candidates.append(manifest_path.parent / path)
        return any(candidate.exists() for candidate in candidates)

    def _resolve_existing_path(self, raw_path: str, manifest_path: Path, project_root: Path | None) -> Path | None:
        path = Path(raw_path)
        candidates = [path] if path.is_absolute() else []
        if project_root is not None:
            candidates.append(project_root / path)
        candidates.append(manifest_path.parent / path)
        for candidate in candidates:
            if candidate.exists():
                return candidate
        return None

    def _same_path(self, raw_path: str, manifest_path: Path) -> bool:
        try:
            return Path(raw_path).resolve() == manifest_path.resolve()
        except OSError:
            return str(Path(raw_path)) == str(manifest_path)

    def _dict(self, value: Any) -> dict[str, Any]:
        return value if isinstance(value, dict) else {}

    def _list(self, value: Any) -> list[Any]:
        return value if isinstance(value, list) else []

    def _string_list(self, value: Any) -> list[str]:
        if not isinstance(value, list):
            return []
        return [str(item) for item in value if str(item)]

    def _status_counts(self, items: list[Any]) -> dict[str, int]:
        counts: dict[str, int] = {}
        for item in items:
            if not isinstance(item, dict):
                continue
            status = str(item.get("status", ""))
            if not status:
                continue
            counts[status] = counts.get(status, 0) + 1
        return counts

    def _dedupe(self, values: list[str]) -> list[str]:
        seen: set[str] = set()
        result: list[str] = []
        for value in values:
            if value in seen:
                continue
            seen.add(value)
            result.append(value)
        return result

    def _as_int(self, value: Any) -> int | None:
        if isinstance(value, bool):
            return None
        if isinstance(value, int):
            return value
        if isinstance(value, str) and value.isdigit():
            return int(value)
        return None

    def _as_float(self, value: Any) -> float | None:
        if isinstance(value, bool):
            return None
        if isinstance(value, (int, float)):
            return float(value)
        if isinstance(value, str):
            try:
                return float(value)
            except ValueError:
                return None
        return None


def verify_manifest(manifest_path: str | Path, *, check_files: bool = True) -> ManifestVerificationResult:
    """Verify one Conductor run manifest."""
    return ManifestVerifier().verify(manifest_path, check_files=check_files)


__all__ = ["ManifestVerificationResult", "ManifestVerifier", "verify_manifest"]
