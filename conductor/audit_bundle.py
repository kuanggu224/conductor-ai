"""Audit bundle verification for archived Conductor project runs."""

from __future__ import annotations

import json
import hashlib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from conductor.replay_verifier import verify_manifest

AUDIT_BUNDLE_SCHEMA_VERSION = "1.0"


@dataclass(slots=True)
class AuditBundleVerificationResult:
    """Structured result for one audit bundle index check."""

    bundle_path: str
    project_id: str = ""
    passed: bool = False
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    files: dict[str, str] = field(default_factory=dict)
    checksums: dict[str, str] = field(default_factory=dict)

    def to_dict(self) -> dict[str, object]:
        return {
            "bundle_path": self.bundle_path,
            "project_id": self.project_id,
            "passed": self.passed,
            "error_count": len(self.errors),
            "warning_count": len(self.warnings),
            "errors": list(self.errors),
            "warnings": list(self.warnings),
            "files": dict(self.files),
            "checksums": dict(self.checksums),
        }


class AuditBundleVerifier:
    """Validate a run audit bundle index without replaying executions."""

    REQUIRED_TOP_LEVEL_FIELDS = ("schema_version", "project_id", "status", "run_profile", "files", "summary")
    REQUIRED_FILE_FIELDS = ("manifest", "report", "manifest_verification", "replay_trace")

    def verify(self, bundle_path: str | Path, *, check_files: bool = True) -> AuditBundleVerificationResult:
        path = Path(bundle_path)
        result = AuditBundleVerificationResult(bundle_path=str(path))
        payload = self._load_json(path, result)
        if payload is None:
            result.passed = False
            return result

        result.project_id = str(payload.get("project_id", ""))
        result.files = self._file_index(payload, path)
        result.checksums = self._checksum_index(payload)
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

    def _file_index(self, payload: dict[str, Any], bundle_path: Path) -> dict[str, str]:
        files = payload.get("files")
        if not isinstance(files, dict):
            return {}
        indexed: dict[str, str] = {}
        for field_name in self.REQUIRED_FILE_FIELDS:
            raw_path = str(files.get(field_name, ""))
            if raw_path:
                indexed[field_name] = str(self._resolve_component_path(raw_path, bundle_path).resolve())
        return indexed

    def _checksum_index(self, payload: dict[str, Any]) -> dict[str, str]:
        checksums = payload.get("checksums")
        if not isinstance(checksums, dict):
            return {}
        return {
            field_name: str(checksums.get(field_name, ""))
            for field_name in self.REQUIRED_FILE_FIELDS
            if str(checksums.get(field_name, ""))
        }

    def _verify_required_fields(self, payload: dict[str, Any], result: AuditBundleVerificationResult) -> None:
        for field_name in self.REQUIRED_TOP_LEVEL_FIELDS:
            if field_name not in payload:
                result.errors.append(f"missing top-level field: {field_name}")
        if not str(payload.get("project_id", "")):
            result.errors.append("project_id must be non-empty")
        schema_version = str(payload.get("schema_version", ""))
        if not schema_version:
            result.errors.append("schema_version must be non-empty")
        elif schema_version != AUDIT_BUNDLE_SCHEMA_VERSION:
            result.warnings.append(
                f"audit bundle schema_version {schema_version} differs from current {AUDIT_BUNDLE_SCHEMA_VERSION}"
            )
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
        self._verify_checksums(bundle_path, files, payload, result)

        manifest_path = self._resolve_component_path(str(files.get("manifest", "")), bundle_path)
        if manifest_path.exists():
            manifest_result = verify_manifest(manifest_path)
            if manifest_result.project_id != result.project_id:
                result.errors.append("manifest.project_id does not match audit bundle project_id")
            result.errors.extend(f"manifest verification: {error}" for error in manifest_result.errors)
            result.warnings.extend(f"manifest verification: {warning}" for warning in manifest_result.warnings)

        verification_path = self._resolve_component_path(str(files.get("manifest_verification", "")), bundle_path)
        if verification_path.exists():
            self._verify_component_json(
                verification_path,
                result,
                component_name="manifest_verification",
                passed_field="passed",
            )

        replay_trace_path = self._resolve_component_path(str(files.get("replay_trace", "")), bundle_path)
        if replay_trace_path.exists():
            self._verify_replay_trace(replay_trace_path, result)

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

    def _verify_replay_trace(self, path: Path, result: AuditBundleVerificationResult) -> None:
        if path.suffix.lower() == ".json":
            self._verify_component_json(path, result, component_name="replay_trace", passed_field="passed")
            return
        try:
            text = path.read_text(encoding="utf-8")
        except OSError as exc:
            result.errors.append(f"replay_trace cannot be read: {exc}")
            return
        if f"# Replay Trace: {result.project_id}" not in text:
            result.errors.append("replay_trace project_id does not match audit bundle project_id")
        if "- Verification: `passed`" not in text:
            result.errors.append("replay_trace verification marker must be passed")

    def _verify_checksums(
        self,
        bundle_path: Path,
        files: dict[str, Any],
        payload: dict[str, Any],
        result: AuditBundleVerificationResult,
    ) -> None:
        checksums = payload.get("checksums")
        if not isinstance(checksums, dict):
            result.warnings.append("checksums must be an object")
            return
        for field_name in self.REQUIRED_FILE_FIELDS:
            expected = str(checksums.get(field_name, ""))
            if not expected:
                result.warnings.append(f"checksums.{field_name} is missing")
                continue
            path = self._resolve_component_path(str(files.get(field_name, "")), bundle_path)
            if path.exists() and path.is_file():
                actual = self._sha256_file(path)
                if actual != expected:
                    result.errors.append(f"checksums.{field_name} does not match file content")

    def _resolve_component_path(self, raw_path: str, bundle_path: Path) -> Path:
        path = Path(raw_path)
        if path.is_absolute():
            return path
        return bundle_path.parent / path

    def _sha256_file(self, path: Path) -> str:
        digest = hashlib.sha256()
        with path.open("rb") as file:
            for chunk in iter(lambda: file.read(1024 * 1024), b""):
                digest.update(chunk)
        return digest.hexdigest()


def verify_audit_bundle(bundle_path: str | Path, *, check_files: bool = True) -> AuditBundleVerificationResult:
    """Verify one Conductor audit bundle index."""
    return AuditBundleVerifier().verify(bundle_path, check_files=check_files)


__all__ = [
    "AUDIT_BUNDLE_SCHEMA_VERSION",
    "AuditBundleVerificationResult",
    "AuditBundleVerifier",
    "verify_audit_bundle",
]
