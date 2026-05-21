"""Audit bundle verification tests."""

from __future__ import annotations

import json
import os
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
    bundle = json.loads(bundle_path.read_text(encoding="utf-8"))

    result = verify_audit_bundle(bundle_path)

    assert result.passed is True
    assert result.errors == []
    assert result.project_id == payload["project_id"]
    assert result.files == bundle["files"]
    assert result.checksums == bundle["checksums"]
    assert result.summary == bundle["summary"]


def test_audit_bundle_verifier_resolves_relative_component_paths(tmp_path, capsys) -> None:
    _, bundle_path = _write_project_audit_bundle(tmp_path, capsys)
    bundle = json.loads(bundle_path.read_text(encoding="utf-8"))
    original_files = dict(bundle["files"])
    bundle["files"] = {
        key: os.path.relpath(value, start=bundle_path.parent)
        for key, value in original_files.items()
    }
    bundle_path.write_text(json.dumps(bundle, ensure_ascii=False, indent=2), encoding="utf-8")

    result = verify_audit_bundle(bundle_path)

    assert result.passed is True
    assert result.files == {key: str(Path(value).resolve()) for key, value in original_files.items()}


def test_audit_bundle_verifier_rejects_missing_component_file(tmp_path, capsys) -> None:
    _, bundle_path = _write_project_audit_bundle(tmp_path, capsys)
    bundle = json.loads(bundle_path.read_text(encoding="utf-8"))
    bundle["files"]["report"] = str(tmp_path / "missing-report.md")
    bundle_path.write_text(json.dumps(bundle, ensure_ascii=False, indent=2), encoding="utf-8")

    result = verify_audit_bundle(bundle_path)

    assert result.passed is False
    assert any("files.report does not exist" in error for error in result.errors)


def test_audit_bundle_verifier_rejects_checksum_mismatch(tmp_path, capsys) -> None:
    _, bundle_path = _write_project_audit_bundle(tmp_path, capsys)
    bundle = json.loads(bundle_path.read_text(encoding="utf-8"))
    report_path = Path(bundle["files"]["report"])
    report_path.write_text("tampered report", encoding="utf-8")

    result = verify_audit_bundle(bundle_path)

    assert result.passed is False
    assert "checksums.report does not match file content" in result.errors


def test_audit_bundle_verifier_warns_for_missing_checksums(tmp_path, capsys) -> None:
    _, bundle_path = _write_project_audit_bundle(tmp_path, capsys)
    bundle = json.loads(bundle_path.read_text(encoding="utf-8"))
    bundle.pop("checksums")
    bundle_path.write_text(json.dumps(bundle, ensure_ascii=False, indent=2), encoding="utf-8")

    result = verify_audit_bundle(bundle_path)

    assert result.passed is True
    assert "checksums must be an object" in result.warnings


def test_audit_bundle_verifier_warns_for_non_current_schema(tmp_path, capsys) -> None:
    _, bundle_path = _write_project_audit_bundle(tmp_path, capsys)
    bundle = json.loads(bundle_path.read_text(encoding="utf-8"))
    bundle["schema_version"] = "0.1"
    bundle_path.write_text(json.dumps(bundle, ensure_ascii=False, indent=2), encoding="utf-8")

    result = verify_audit_bundle(bundle_path)

    assert result.passed is True
    assert "audit bundle schema_version 0.1 differs from current 1.2" in result.warnings


def test_audit_bundle_verifier_rejects_pending_test_scope_mismatch(tmp_path, capsys) -> None:
    _, bundle_path = _write_project_audit_bundle(tmp_path, capsys)
    bundle = json.loads(bundle_path.read_text(encoding="utf-8"))
    manifest_path = Path(bundle["files"]["manifest"])
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["summary"]["pending_test_scope"] = ["ui_validation"]
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    bundle["summary"]["pending_test_scope"] = ["api_validation"]
    bundle_path.write_text(json.dumps(bundle, ensure_ascii=False, indent=2), encoding="utf-8")

    result = verify_audit_bundle(bundle_path)

    assert result.passed is False
    assert "checksums.manifest does not match file content" in result.errors
    assert "summary.pending_test_scope does not match manifest summary.pending_test_scope" in result.errors


def test_audit_bundle_verifier_rejects_manifest_summary_mismatch(tmp_path, capsys) -> None:
    _, bundle_path = _write_project_audit_bundle(tmp_path, capsys)
    bundle = json.loads(bundle_path.read_text(encoding="utf-8"))
    bundle["summary"]["manifest_schema_version"] = "0.0"
    bundle["summary"]["manifest_final_status"] = "blocked"
    bundle_path.write_text(json.dumps(bundle, ensure_ascii=False, indent=2), encoding="utf-8")

    result = verify_audit_bundle(bundle_path)

    assert result.passed is False
    assert "summary.manifest_schema_version does not match manifest.schema_version" in result.errors
    assert "summary.manifest_final_status does not match manifest.final_status" in result.errors


def test_audit_bundle_verifier_rejects_human_control_summary_mismatch(tmp_path, capsys) -> None:
    _, bundle_path = _write_project_audit_bundle(tmp_path, capsys)
    bundle = json.loads(bundle_path.read_text(encoding="utf-8"))
    manifest_path = Path(bundle["files"]["manifest"])
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    human_action = {
        "id": "human-action-pause",
        "project_id": manifest["project_id"],
        "action": "pause",
        "actor": "operator",
        "reason": "inspect delivery",
        "stage": manifest["current_stage"],
        "workitem_id": "",
        "payload": {},
        "created_at": "2026-01-01T00:00:00+00:00",
    }
    manifest["human_control_actions"] = [human_action]
    manifest["summary"]["human_control_action_count"] = 1
    manifest["resume_cursor"]["next_action"] = "human_hold"
    manifest["resume_cursor"]["blocked"] = True
    manifest["resume_cursor"]["terminal"] = False
    manifest["resume_cursor"]["blockers"] = ["human_paused: inspect delivery"]
    manifest["resume_cursor"]["active_human_control_action"] = human_action
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    bundle["summary"]["human_control_action_count"] = 0
    bundle["summary"]["active_human_control_action"] = {}
    bundle_path.write_text(json.dumps(bundle, ensure_ascii=False, indent=2), encoding="utf-8")

    result = verify_audit_bundle(bundle_path)

    assert result.passed is False
    assert "checksums.manifest does not match file content" in result.errors
    assert "summary.human_control_action_count does not match manifest summary.human_control_action_count" in result.errors
    assert (
        "summary.active_human_control_action does not match manifest resume_cursor.active_human_control_action"
        in result.errors
    )


def test_audit_bundle_verifier_rejects_malformed_human_control_summary(tmp_path, capsys) -> None:
    _, bundle_path = _write_project_audit_bundle(tmp_path, capsys)
    bundle = json.loads(bundle_path.read_text(encoding="utf-8"))
    bundle["summary"]["human_control_action_count"] = "many"
    bundle["summary"]["active_human_control_action"] = "human-action-pause"
    bundle_path.write_text(json.dumps(bundle, ensure_ascii=False, indent=2), encoding="utf-8")

    result = verify_audit_bundle(bundle_path)

    assert result.passed is False
    assert "summary.human_control_action_count must be an integer" in result.errors
    assert "summary.active_human_control_action must be an object" in result.errors


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
    assert payload["files"]["manifest"]
    assert payload["checksums"]["manifest"]


def test_verify_audit_bundle_cli_accepts_bundle_directory(tmp_path, capsys) -> None:
    payload, _ = _write_project_audit_bundle(tmp_path, capsys)

    exit_code = verify_audit_bundle_main([str(tmp_path)])
    captured = capsys.readouterr()

    assert exit_code == 0
    report = json.loads(captured.out)
    assert report["passed"] is True
    assert report["bundle_count"] == 1
    assert report["failed_bundle_count"] == 0
    assert report["warning_bundle_count"] == 0
    assert report["results"][0]["project_id"] == payload["project_id"]


def test_verify_audit_bundle_cli_fails_for_directory_without_bundles(tmp_path, capsys) -> None:
    empty_dir = tmp_path / "empty"
    empty_dir.mkdir()

    exit_code = verify_audit_bundle_main([str(empty_dir)])
    captured = capsys.readouterr()

    assert exit_code == 2
    report = json.loads(captured.out)
    assert report["passed"] is False
    assert report["bundle_count"] == 0
    assert report["failed_bundle_count"] == 1
    assert "no audit bundles found" in report["errors"][0]


def test_verify_audit_bundle_cli_directory_can_fail_on_warnings(tmp_path, capsys) -> None:
    _, bundle_path = _write_project_audit_bundle(tmp_path, capsys)
    bundle = json.loads(bundle_path.read_text(encoding="utf-8"))
    bundle["schema_version"] = "0.1"
    bundle_path.write_text(json.dumps(bundle, ensure_ascii=False, indent=2), encoding="utf-8")

    exit_code = verify_audit_bundle_main([str(tmp_path), "--fail-on-warnings"])
    captured = capsys.readouterr()

    assert exit_code == 2
    report = json.loads(captured.out)
    assert report["passed"] is False
    assert report["bundle_count"] == 1
    assert report["failed_bundle_count"] == 0
    assert report["warning_bundle_count"] == 1
    assert report["warning_count"] > 0


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
    bundle["schema_version"] = "0.1"
    bundle_path.write_text(json.dumps(bundle, ensure_ascii=False, indent=2), encoding="utf-8")

    exit_code = verify_audit_bundle_main([str(bundle_path), "--fail-on-warnings"])
    captured = capsys.readouterr()

    assert exit_code == 2
    payload = json.loads(captured.out)
    assert payload["passed"] is True
    assert any("audit bundle schema_version 0.1 differs from current 1.2" in warning for warning in payload["warnings"])


def test_verify_audit_bundle_cli_exits_two_for_invalid_bundle(tmp_path, capsys) -> None:
    bundle_path = tmp_path / "bad.audit.json"
    bundle_path.write_text(json.dumps({"project_id": ""}, ensure_ascii=False), encoding="utf-8")

    exit_code = verify_audit_bundle_main([str(bundle_path)])
    captured = capsys.readouterr()

    assert exit_code == 2
    assert "project_id must be non-empty" in captured.out
