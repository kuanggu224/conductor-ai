"""Audit bundle verification tests."""

from __future__ import annotations

import json
from pathlib import Path

from app import run_project
from app.verify_audit_bundle import main as verify_audit_bundle_main
from conductor.audit_bundle import verify_audit_bundle


def _write_project_audit_bundle(tmp_path: Path, capsys) -> tuple[dict[str, object], Path]:
    exit_code = run_project.main(
        [
            "--project-root",
            str(tmp_path),
            "--requirement",
            "Build a small reading list",
            "--max-steps",
            "1",
            "--skip-preflight-gate",
            "--write-audit-bundle",
        ]
    )
    payload = json.loads(capsys.readouterr().out)
    assert exit_code == 1
    return payload, Path(str(payload["audit_bundle"]["path"]))


def test_audit_bundle_verifier_accepts_run_project_bundle(tmp_path, capsys) -> None:
    payload, bundle_path = _write_project_audit_bundle(tmp_path, capsys)

    result = verify_audit_bundle(bundle_path)

    assert result.passed is True
    assert result.errors == []
    assert result.project_id == payload["project_id"]


def test_audit_bundle_verifier_rejects_missing_component_file(tmp_path, capsys) -> None:
    _, bundle_path = _write_project_audit_bundle(tmp_path, capsys)
    bundle = json.loads(bundle_path.read_text(encoding="utf-8"))
    bundle["files"]["report"] = str(tmp_path / "missing-report.md")
    bundle_path.write_text(json.dumps(bundle, ensure_ascii=False, indent=2), encoding="utf-8")

    result = verify_audit_bundle(bundle_path)

    assert result.passed is False
    assert any("files.report does not exist" in error for error in result.errors)


def test_audit_bundle_verifier_warns_for_non_current_schema(tmp_path, capsys) -> None:
    _, bundle_path = _write_project_audit_bundle(tmp_path, capsys)
    bundle = json.loads(bundle_path.read_text(encoding="utf-8"))
    bundle["schema_version"] = "0.1"
    bundle_path.write_text(json.dumps(bundle, ensure_ascii=False, indent=2), encoding="utf-8")

    result = verify_audit_bundle(bundle_path)

    assert result.passed is True
    assert "audit bundle schema_version 0.1 differs from current 1.0" in result.warnings


def test_audit_bundle_verifier_reruns_manifest_verification(tmp_path, capsys) -> None:
    _, bundle_path = _write_project_audit_bundle(tmp_path, capsys)
    bundle = json.loads(bundle_path.read_text(encoding="utf-8"))
    manifest_path = Path(bundle["files"]["manifest"])
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["project_id"] = ""
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")

    result = verify_audit_bundle(bundle_path)

    assert result.passed is False
    assert "manifest.project_id does not match audit bundle project_id" in result.errors
    assert any("manifest verification: project_id must be non-empty" in error for error in result.errors)


def test_audit_bundle_verifier_rejects_mismatched_replay_trace(tmp_path, capsys) -> None:
    payload, bundle_path = _write_project_audit_bundle(tmp_path, capsys)
    bundle = json.loads(bundle_path.read_text(encoding="utf-8"))
    replay_trace_path = Path(bundle["files"]["replay_trace"])
    replay_trace_path.write_text(
        "# Replay Trace: other-project\n\n- Verification: `passed`\n",
        encoding="utf-8",
    )

    result = verify_audit_bundle(bundle_path)

    assert result.passed is False
    assert result.project_id == payload["project_id"]
    assert "replay_trace project_id does not match audit bundle project_id" in result.errors


def test_audit_bundle_verifier_rejects_failed_replay_trace_marker(tmp_path, capsys) -> None:
    payload, bundle_path = _write_project_audit_bundle(tmp_path, capsys)
    bundle = json.loads(bundle_path.read_text(encoding="utf-8"))
    replay_trace_path = Path(bundle["files"]["replay_trace"])
    replay_trace_path.write_text(
        f"# Replay Trace: {payload['project_id']}\n\n- Verification: `failed`\n",
        encoding="utf-8",
    )

    result = verify_audit_bundle(bundle_path)

    assert result.passed is False
    assert "replay_trace verification marker must be passed" in result.errors


def test_verify_audit_bundle_cli_exits_zero_for_valid_bundle(tmp_path, capsys) -> None:
    _, bundle_path = _write_project_audit_bundle(tmp_path, capsys)

    exit_code = verify_audit_bundle_main([str(bundle_path)])
    captured = capsys.readouterr()

    assert exit_code == 0
    payload = json.loads(captured.out)
    assert payload["passed"] is True


def test_verify_audit_bundle_cli_writes_output_file(tmp_path, capsys) -> None:
    _, bundle_path = _write_project_audit_bundle(tmp_path, capsys)
    output_path = tmp_path / "audit" / "bundle-verification.json"

    exit_code = verify_audit_bundle_main([str(bundle_path), "--output", str(output_path)])
    captured = capsys.readouterr()

    assert exit_code == 0
    summary = json.loads(captured.out)
    assert summary["ok"] is True
    assert summary["output_path"] == str(output_path.resolve())
    report = json.loads(output_path.read_text(encoding="utf-8"))
    assert report["passed"] is True


def test_verify_audit_bundle_cli_can_fail_on_manifest_warnings(tmp_path, capsys) -> None:
    _, bundle_path = _write_project_audit_bundle(tmp_path, capsys)
    bundle = json.loads(bundle_path.read_text(encoding="utf-8"))
    manifest_path = Path(bundle["files"]["manifest"])
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["schema_version"] = "1.0"
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")

    exit_code = verify_audit_bundle_main([str(bundle_path), "--fail-on-warnings"])
    captured = capsys.readouterr()

    assert exit_code == 2
    payload = json.loads(captured.out)
    assert payload["passed"] is True
    assert any("manifest schema_version 1.0 differs from current 1.28" in warning for warning in payload["warnings"])


def test_verify_audit_bundle_cli_exits_two_for_invalid_bundle(tmp_path, capsys) -> None:
    bundle_path = tmp_path / "bad.audit.json"
    bundle_path.write_text(json.dumps({"project_id": ""}, ensure_ascii=False), encoding="utf-8")

    exit_code = verify_audit_bundle_main([str(bundle_path)])
    captured = capsys.readouterr()

    assert exit_code == 2
    assert "project_id must be non-empty" in captured.out
