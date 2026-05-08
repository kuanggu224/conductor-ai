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
        ):
            if key in payload and not isinstance(payload.get(key), list):
                result.errors.append(f"{key} must be a list")
        for key in ("summary", "resume_cursor", "files"):
            if key in payload and not isinstance(payload.get(key), dict):
                result.errors.append(f"{key} must be an object")

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

        changed_files = self._list(summary.get("changed_files"))
        if "changed_file_count" in summary:
            actual = self._as_int(summary.get("changed_file_count"))
            if actual is None:
                result.errors.append("summary.changed_file_count must be an integer")
            elif actual != len(changed_files):
                result.errors.append(
                    f"summary.changed_file_count={actual} does not match len(summary.changed_files)={len(changed_files)}"
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
        if final_status not in {"completed", "blocked"} and cursor.get("terminal") is True:
            result.errors.append("non-terminal manifest cannot have resume_cursor.terminal=true")

        workitem_ids = self._id_set(self._list(payload.get("workitems")))
        for cursor_key in self.CURSOR_WORKITEM_LISTS:
            for workitem_id in self._string_list(cursor.get(cursor_key)):
                if workitem_id not in workitem_ids:
                    result.errors.append(f"resume_cursor.{cursor_key} references unknown WorkItem: {workitem_id}")
        last_execution_workitem_id = str(cursor.get("last_execution_workitem_id", ""))
        if last_execution_workitem_id and last_execution_workitem_id not in workitem_ids:
            result.errors.append(
                f"resume_cursor.last_execution_workitem_id references unknown WorkItem: {last_execution_workitem_id}"
            )

    def _verify_links(self, payload: dict[str, Any], result: ManifestVerificationResult) -> None:
        project_id = str(payload.get("project_id", ""))
        workitems = self._list(payload.get("workitems"))
        artifacts = self._list(payload.get("artifacts"))
        executions = self._list(payload.get("executions"))
        task_assignments = self._list(payload.get("task_assignments"))

        workitem_ids = self._ids_with_duplicate_check("workitems", workitems, result)
        artifact_ids = self._ids_with_duplicate_check("artifacts", artifacts, result)

        for workitem in workitems:
            if not isinstance(workitem, dict):
                continue
            workitem_id = str(workitem.get("id", ""))
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
            if workitem_id and workitem_id not in workitem_ids:
                result.errors.append(f"execution references unknown WorkItem: {workitem_id}")
            for artifact_id in self._string_list(execution.get("artifact_ids", []) if isinstance(execution, dict) else []):
                if artifact_id not in artifact_ids:
                    result.errors.append(f"execution references unknown Artifact: {artifact_id}")

        for artifact in artifacts:
            if not isinstance(artifact, dict):
                continue
            artifact_id = str(artifact.get("id", ""))
            artifact_workitem_id = str(artifact.get("workitem_id", ""))
            if artifact_workitem_id and artifact_workitem_id not in workitem_ids:
                result.errors.append(f"artifact references unknown WorkItem: {artifact_workitem_id}")
            artifact_project_id = str(artifact.get("project_id", ""))
            if artifact_project_id and artifact_project_id != project_id:
                result.errors.append(f"artifact.project_id does not match manifest.project_id: {artifact.get('id', '')}")
            for field_name in ("parent_artifact_id", "review_of"):
                linked_id = str(artifact.get(field_name, ""))
                if linked_id and linked_id == artifact_id:
                    result.warnings.append(f"artifact {artifact_id} has self-referential {field_name}")
                elif linked_id and linked_id not in artifact_ids:
                    result.warnings.append(f"artifact {artifact.get('id', '')} has unresolved {field_name}: {linked_id}")
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
            if workitem_id and workitem_id not in workitem_ids:
                result.errors.append(f"task assignment references unknown WorkItem: {workitem_id}")
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
        for label in ("log", "report", "preflight_gate"):
            raw_path = str(files.get(label, ""))
            if raw_path and not self._path_exists(raw_path, manifest_path, project_root):
                result.warnings.append(f"files.{label} does not exist: {raw_path}")

        indexed_manifest_path = str(files.get("manifest", ""))
        if indexed_manifest_path and not self._same_path(indexed_manifest_path, manifest_path):
            result.warnings.append(f"files.manifest differs from verified path: {indexed_manifest_path}")

        for field_name in ("artifact_files", "task_prompt_files"):
            for raw_path in self._string_list(payload.get(field_name, [])):
                if raw_path and not self._path_exists(raw_path, manifest_path, project_root):
                    result.warnings.append(f"{field_name} entry does not exist: {raw_path}")

        artifact_files = set(self._string_list(payload.get("artifact_files", [])))
        for artifact in self._list(payload.get("artifacts")):
            if not isinstance(artifact, dict):
                continue
            artifact_path = str(artifact.get("path", ""))
            if artifact_path and artifact_path not in artifact_files:
                result.warnings.append(f"artifact path is not indexed in artifact_files: {artifact_path}")

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

    def _id_set(self, records: list[Any]) -> set[str]:
        return {str(record.get("id", "")) for record in records if isinstance(record, dict) and record.get("id")}

    def _path_exists(self, raw_path: str, manifest_path: Path, project_root: Path | None) -> bool:
        path = Path(raw_path)
        candidates = [path] if path.is_absolute() else []
        if project_root is not None:
            candidates.append(project_root / path)
        candidates.append(manifest_path.parent / path)
        return any(candidate.exists() for candidate in candidates)

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

    def _as_int(self, value: Any) -> int | None:
        if isinstance(value, bool):
            return None
        if isinstance(value, int):
            return value
        if isinstance(value, str) and value.isdigit():
            return int(value)
        return None


def verify_manifest(manifest_path: str | Path, *, check_files: bool = True) -> ManifestVerificationResult:
    """Verify one Conductor run manifest."""
    return ManifestVerifier().verify(manifest_path, check_files=check_files)


__all__ = ["ManifestVerificationResult", "ManifestVerifier", "verify_manifest"]
