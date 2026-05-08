"""Audit bundle verification for archived Conductor project runs."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass(slots=True)
class AuditBundleVerificationResult:
    """Structured result for one audit bundle index check."""

    bundle_path: str
    project_id: str = ""
    passed: bool = False
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, object]:
        return {
            "bundle_path": self.bundle_path,
            "project_id": self.project_id,
            "passed": self.passed,
            "error_count": len(self.errors),
            "warning_count": len(self.warnings),
            "errors": list(self.errors),
            "warnings": list(self.warnings),
        }


class AuditBundleVerifier:
    """Validate a run audit bundle index without replaying executions."""

    REQUIRED_TOP_LEVEL_FIELDS = ("project_id", "status", "run_profile", "files", "summary")
    REQUIRED_FILE_FIELDS = ("manifest", "report", "manifest_verification", "replay_trace")

    def verify(self, bundle_path: str | Path, *, check_files: bool = True) -> AuditBundleVerificationResult:
        path = Path(bundle_path)
        result = AuditBundleVerificationResult(bundle_path=str(path))
        payload = self._load_json(path, result)
        if payload is None:
            result.passed = False
            return result

        result.project_id = str(payload.get("project_id", ""))
        self._verify_required_fields(payload, result)
        self._verify_summary(payload, result)
        if check_files:
            self._verify_files(path, payload, result)
        result.passed = not result.errors
        return result

    def _load_json(self, path: Path, result: AuditBundleVerificationResult) -> dict[str, Any] | None:
        if not path.exists():
            result.errors.append(f"audit bundle file does not exist: {path}")
            return None
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            result.errors.append(f"audit bundle is not valid JSON: {exc}")
            return None
        if not isinstance(payload, dict):
            result.errors.append("audit bundle root must be a JSON object")
            return None
        return payload

    def _verify_required_fields(self, payload: dict[str, Any], result: AuditBundleVerificationResult) -> None:
        for field_name in self.REQUIRED_TOP_LEVEL_FIELDS:
            if field_name not in payload:
                result.errors.append(f"missing top-level field: {field_name}")
        if not str(payload.get("project_id", "")):
            result.errors.append("project_id must be non-empty")
        files = payload.get("files")
        if not isinstance(files, dict):
            result.errors.append("files must be an object")
            return
        for field_name in self.REQUIRED_FILE_FIELDS:
            if not str(files.get(field_name, "")):
                result.errors.append(f"files.{field_name} must be non-empty")
        if not isinstance(payload.get("summary"), dict):
            result.errors.append("summary must be an object")

    def _verify_summary(self, payload: dict[str, Any], result: AuditBundleVerificationResult) -> None:
        summary = payload.get("summary")
        if not isinstance(summary, dict):
            return
        if summary.get("manifest_verification_passed") is not True:
            result.errors.append("summary.manifest_verification_passed must be true")
        if summary.get("replay_trace_passed") is not True:
            result.errors.append("summary.replay_trace_passed must be true")

    def _verify_files(self, bundle_path: Path, payload: dict[str, Any], result: AuditBundleVerificationResult) -> None:
        files = payload.get("files")
        if not isinstance(files, dict):
            return
        for field_name in self.REQUIRED_FILE_FIELDS:
            raw_path = str(files.get(field_name, ""))
            if raw_path and not self._resolve_component_path(raw_path, bundle_path).exists():
                result.errors.append(f"files.{field_name} does not exist: {raw_path}")

        verification_path = self._resolve_component_path(str(files.get("manifest_verification", "")), bundle_path)
        if verification_path.exists():
            self._verify_component_json(
                verification_path,
                result,
                component_name="manifest_verification",
                passed_field="passed",
            )

    def _verify_component_json(
        self,
        path: Path,
        result: AuditBundleVerificationResult,
        *,
        component_name: str,
        passed_field: str,
    ) -> None:
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            result.errors.append(f"{component_name} is not valid JSON: {exc}")
            return
        if not isinstance(payload, dict):
            result.errors.append(f"{component_name} root must be a JSON object")
            return
        if str(payload.get("project_id", "")) != result.project_id:
            result.errors.append(f"{component_name}.project_id does not match audit bundle project_id")
        if payload.get(passed_field) is not True:
            result.errors.append(f"{component_name}.{passed_field} must be true")

    def _resolve_component_path(self, raw_path: str, bundle_path: Path) -> Path:
        path = Path(raw_path)
        if path.is_absolute():
            return path
        return bundle_path.parent / path


def verify_audit_bundle(bundle_path: str | Path, *, check_files: bool = True) -> AuditBundleVerificationResult:
    """Verify one Conductor audit bundle index."""
    return AuditBundleVerifier().verify(bundle_path, check_files=check_files)


__all__ = ["AuditBundleVerificationResult", "AuditBundleVerifier", "verify_audit_bundle"]
