"""Tests for the UTF-8 project runner entrypoint."""

import json
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from app import run_project
from app.run_project import _build_cli_config, _run_preflight_gate, _resolve_project_root, build_parser
from conductor.agents.llm import LLMHTTPConfig
from conductor.config.execution import resolve_run_profile
from conductor.config.llm import LLMRuntimeConfig, LLMUsagePolicy
from conductor.state.file_store import FileStateStore
from conductor.task_center.service import TaskCenterService


def test_run_project_parser_accepts_requirement_file() -> None:
    args = build_parser().parse_args(["--requirement-file", "requirement.txt", "--project-root", "demo"])

    assert args.requirement_file == "requirement.txt"
    assert args.project_root == "demo"
    assert args.codex is False
    assert args.run_profile == "mock"


def test_run_project_parser_accepts_project_name() -> None:
    args = build_parser().parse_args(["--requirement", "demo", "--project-root", "C:/tmp/conductor_test", "--project-name", "case-1"])

    assert args.project_name == "case-1"


def test_resolve_project_root_uses_child_for_conductor_test_root() -> None:
    resolved = _resolve_project_root("C:/tmp/conductor_test", "case-1")

    assert resolved.as_posix().endswith("/conductor_test/case-1")


def test_run_project_parser_accepts_run_profile() -> None:
    args = build_parser().parse_args(["--requirement", "demo", "--run-profile", "full_cli", "--codex"])

    assert args.run_profile == "full_cli"
    assert args.codex is True


def test_run_project_parser_accepts_agent_cli() -> None:
    args = build_parser().parse_args(["--requirement", "demo", "--run-profile", "design_cli_only", "--agent-cli", "opencode"])

    assert args.run_profile == "design_cli_only"
    assert args.agent_cli == "opencode"


def test_run_project_parser_accepts_aspirecode_agent_cli() -> None:
    args = build_parser().parse_args(
        [
            "--requirement",
            "demo",
            "--run-profile",
            "design_cli_only",
            "--agent-cli",
            "aspirecode",
            "--aspirecode-model",
            "lmstudio-local/qwen3.6-35b-a3b",
        ]
    )

    assert args.agent_cli == "aspirecode"
    assert args.aspirecode_model == "lmstudio-local/qwen3.6-35b-a3b"


def test_run_project_parser_accepts_llm_harness() -> None:
    args = build_parser().parse_args(
        [
            "--requirement",
            "demo",
            "--run-profile",
            "design_cli_only",
            "--llm-harness",
            "local",
            "--llm-base-url",
            "http://127.0.0.1:1234/v1",
            "--llm-model",
            "qwen3.6-35b-a3b",
            "--llm-reasoning-effort",
            "none",
        ]
    )

    assert args.llm_harness == "local"
    assert args.llm_base_url == "http://127.0.0.1:1234/v1"
    assert args.llm_model == "qwen3.6-35b-a3b"
    assert args.llm_reasoning_effort == "none"


def test_run_project_parser_accepts_collaboration_overrides() -> None:
    args = build_parser().parse_args(
        [
            "--requirement",
            "demo",
            "--collaboration-max-rounds",
            "1",
            "--static-requirement-review",
            "--diagnose-cli",
            "--diagnose-llm",
        ]
    )

    assert args.collaboration_max_rounds == 1
    assert args.static_requirement_review is True
    assert args.diagnose_cli is True
    assert args.diagnose_llm is True


def test_run_project_parser_accepts_skip_preflight_gate() -> None:
    args = build_parser().parse_args(["--requirement", "demo", "--skip-preflight-gate"])

    assert args.skip_preflight_gate is True


def test_run_project_parser_accepts_preflight_only() -> None:
    args = build_parser().parse_args(["--run-profile", "design_cli_only", "--preflight-only"])

    assert args.preflight_only is True


def test_run_project_parser_accepts_resume_project_id() -> None:
    args = build_parser().parse_args(["--project-root", "demo", "--resume-project-id", "project-123"])

    assert args.resume_project_id == "project-123"


def test_run_project_parser_accepts_release_stale_tasks() -> None:
    args = build_parser().parse_args(
        [
            "--project-root",
            "demo",
            "--resume-project-id",
            "project-123",
            "--release-stale-tasks",
            "--stale-after-seconds",
            "10",
            "--stale-release-reason",
            "resume cleanup",
        ]
    )

    assert args.release_stale_tasks is True
    assert args.stale_after_seconds == 10
    assert args.stale_release_reason == "resume cleanup"


def test_run_project_parser_accepts_replay_trace_options() -> None:
    args = build_parser().parse_args(
        [
            "--requirement",
            "demo",
            "--write-replay-trace",
            "--replay-trace-format",
            "json",
            "--replay-trace-output",
            "trace.json",
        ]
    )

    assert args.write_replay_trace is True
    assert args.replay_trace_format == "json"
    assert args.replay_trace_output == "trace.json"


def test_run_project_parser_accepts_audit_bundle_option() -> None:
    args = build_parser().parse_args(
        [
            "--requirement",
            "demo",
            "--write-audit-bundle",
            "--audit-bundle-output",
            "audit/bundle.json",
            "--audit-fail-on-warnings",
        ]
    )

    assert args.write_audit_bundle is True
    assert args.audit_bundle_output == "audit/bundle.json"
    assert args.audit_fail_on_warnings is True


def test_run_project_parser_accepts_manifest_verification_output_options() -> None:
    args = build_parser().parse_args(
        [
            "--requirement",
            "demo",
            "--write-manifest-verification",
            "--manifest-verification-output",
            "audit/verification.json",
        ]
    )

    assert args.write_manifest_verification is True
    assert args.manifest_verification_output == "audit/verification.json"


def test_run_project_can_resume_existing_project(tmp_path, capsys) -> None:
    first_exit = run_project.main(
        [
            "--project-root",
            str(tmp_path),
            "--requirement",
            "Build a small reading list",
            "--max-steps",
            "0",
            "--skip-preflight-gate",
        ]
    )
    first_payload = json.loads(capsys.readouterr().out)

    second_exit = run_project.main(
        [
            "--project-root",
            str(tmp_path),
            "--resume-project-id",
            first_payload["project_id"],
            "--max-steps",
            "1",
            "--skip-preflight-gate",
        ]
    )
    second_payload = json.loads(capsys.readouterr().out)
    resumed_state = FileStateStore(tmp_path / ".conductor" / "state").get_state(first_payload["project_id"])

    assert first_exit == 1
    assert second_exit == 1
    assert second_payload["project_id"] == first_payload["project_id"]
    assert second_payload["resumed"] is True
    assert second_payload["status"] == "in_progress"
    assert len(first_payload["workitems"]) == 1
    assert len(resumed_state.executions) == 1
    assert second_payload["manifest_path"].endswith(f"{first_payload['project_id']}.manifest.json")
    assert second_payload["manifest_verification"]["passed"] is True
    assert second_payload["manifest_verification"]["error_count"] == 0


def test_run_project_can_write_replay_trace(tmp_path, capsys) -> None:
    exit_code = run_project.main(
        [
            "--project-root",
            str(tmp_path),
            "--requirement",
            "Build a small reading list",
            "--max-steps",
            "1",
            "--skip-preflight-gate",
            "--write-replay-trace",
        ]
    )
    payload = json.loads(capsys.readouterr().out)
    trace_path = tmp_path / ".conductor" / "replay" / f"{payload['project_id']}.replay.md"

    assert exit_code == 1
    assert payload["replay_trace"]["path"] == str(trace_path)
    assert payload["replay_trace"]["format"] == "markdown"
    assert payload["replay_trace"]["passed"] is True
    assert payload["replay_trace"]["event_count"] > 0
    assert trace_path.exists()
    assert "# Replay Trace:" in trace_path.read_text(encoding="utf-8")


def test_run_project_can_write_manifest_verification_report(tmp_path, capsys) -> None:
    exit_code = run_project.main(
        [
            "--project-root",
            str(tmp_path),
            "--requirement",
            "Build a small reading list",
            "--max-steps",
            "1",
            "--skip-preflight-gate",
            "--write-manifest-verification",
        ]
    )
    payload = json.loads(capsys.readouterr().out)
    report_path = tmp_path / ".conductor" / "replay" / f"{payload['project_id']}.verification.json"
    report = json.loads(report_path.read_text(encoding="utf-8"))

    assert exit_code == 1
    assert payload["manifest_verification_report"]["path"] == str(report_path)
    assert payload["manifest_verification_report"]["format"] == "json"
    assert payload["manifest_verification_report"]["passed"] is True
    assert payload["manifest_verification_report"]["error_count"] == 0
    assert report["passed"] is True
    assert report["project_id"] == payload["project_id"]


def test_run_project_can_write_audit_bundle(tmp_path, capsys) -> None:
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
    verification_path = tmp_path / ".conductor" / "replay" / f"{payload['project_id']}.verification.json"
    trace_path = tmp_path / ".conductor" / "replay" / f"{payload['project_id']}.replay.md"
    bundle_path = tmp_path / ".conductor" / "replay" / f"{payload['project_id']}.audit.json"
    bundle = json.loads(bundle_path.read_text(encoding="utf-8"))

    assert exit_code == 1
    assert payload["manifest_verification_report"]["path"] == str(verification_path)
    assert payload["manifest_verification_report"]["passed"] is True
    assert payload["replay_trace"]["path"] == str(trace_path)
    assert payload["replay_trace"]["passed"] is True
    assert payload["audit_bundle"]["path"] == str(bundle_path)
    assert payload["audit_bundle"]["manifest_verification_passed"] is True
    assert payload["audit_bundle"]["replay_trace_passed"] is True
    assert payload["audit_bundle_verification"]["passed"] is True
    assert payload["audit_bundle_verification"]["project_id"] == payload["project_id"]
    assert verification_path.exists()
    assert trace_path.exists()
    assert bundle["schema_version"] == "1.0"
    assert bundle["files"]["manifest"] == payload["manifest_path"]
    assert bundle["files"]["report"] == payload["report_path"]
    assert bundle["files"]["manifest_verification"] == str(verification_path)
    assert bundle["files"]["replay_trace"] == str(trace_path)
    assert len(bundle["checksums"]["manifest"]) == 64
    assert len(bundle["checksums"]["report"]) == 64
    assert len(bundle["checksums"]["manifest_verification"]) == 64
    assert len(bundle["checksums"]["replay_trace"]) == 64


def test_run_project_returns_two_when_audit_bundle_verification_fails(monkeypatch, tmp_path, capsys) -> None:
    monkeypatch.setattr(
        run_project,
        "verify_audit_bundle",
        lambda _: SimpleNamespace(to_dict=lambda: {"passed": False, "errors": ["audit failed"]}),
    )

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

    assert exit_code == 2
    assert payload["audit_bundle_verification"]["passed"] is False


def test_run_project_can_fail_audit_bundle_warnings(monkeypatch, tmp_path, capsys) -> None:
    monkeypatch.setattr(
        run_project,
        "verify_audit_bundle",
        lambda _: SimpleNamespace(to_dict=lambda: {"passed": True, "warning_count": 1, "warnings": ["audit warning"]}),
    )

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
            "--audit-fail-on-warnings",
        ]
    )
    payload = json.loads(capsys.readouterr().out)

    assert exit_code == 2
    assert payload["audit_bundle_verification"]["warning_count"] == 1


def test_run_project_resolves_relative_audit_bundle_output_under_project_root(tmp_path, capsys) -> None:
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
            "--audit-bundle-output",
            "audit/bundle.json",
        ]
    )
    payload = json.loads(capsys.readouterr().out)
    bundle_path = tmp_path / "audit" / "bundle.json"

    assert exit_code == 1
    assert payload["audit_bundle"]["path"] == str(bundle_path.resolve())
    assert bundle_path.exists()


def test_run_project_resolves_relative_replay_trace_output_under_project_root(tmp_path, capsys) -> None:
    exit_code = run_project.main(
        [
            "--project-root",
            str(tmp_path),
            "--requirement",
            "Build a small reading list",
            "--max-steps",
            "1",
            "--skip-preflight-gate",
            "--write-replay-trace",
            "--replay-trace-output",
            "audit/trace.md",
        ]
    )
    payload = json.loads(capsys.readouterr().out)
    trace_path = tmp_path / "audit" / "trace.md"

    assert exit_code == 1
    assert payload["replay_trace"]["path"] == str(trace_path.resolve())
    assert trace_path.exists()


def test_run_project_resolves_relative_manifest_verification_output_under_project_root(tmp_path, capsys) -> None:
    exit_code = run_project.main(
        [
            "--project-root",
            str(tmp_path),
            "--requirement",
            "Build a small reading list",
            "--max-steps",
            "1",
            "--skip-preflight-gate",
            "--write-manifest-verification",
            "--manifest-verification-output",
            "audit/verification.json",
        ]
    )
    payload = json.loads(capsys.readouterr().out)
    report_path = tmp_path / "audit" / "verification.json"

    assert exit_code == 1
    assert payload["manifest_verification_report"]["path"] == str(report_path.resolve())
    assert report_path.exists()


def test_run_project_can_release_stale_tasks_before_resume(tmp_path, capsys) -> None:
    exit_code = run_project.main(
        [
            "--project-root",
            str(tmp_path),
            "--requirement",
            "Build a small reading list",
            "--max-steps",
            "0",
            "--skip-preflight-gate",
        ]
    )
    payload = json.loads(capsys.readouterr().out)
    assert exit_code == 1

    store = FileStateStore(tmp_path / ".conductor" / "state")
    initial_state = store.get_state(payload["project_id"])
    service = TaskCenterService(store)
    claimed = service.claim(payload["project_id"], initial_state.task_assignments[0].id, agent_id="agent-worker")
    stale_time = (datetime.now(timezone.utc) - timedelta(hours=2)).isoformat()
    stale_assignment = replace(claimed.assignment, claimed_at=stale_time, last_heartbeat_at=stale_time)
    store.upsert_task_assignment(payload["project_id"], stale_assignment)

    resumed_exit = run_project.main(
        [
            "--project-root",
            str(tmp_path),
            "--resume-project-id",
            payload["project_id"],
            "--max-steps",
            "0",
            "--skip-preflight-gate",
            "--release-stale-tasks",
            "--stale-after-seconds",
            "1",
            "--stale-release-reason",
            "resume cleanup",
        ]
    )
    resumed_payload = json.loads(capsys.readouterr().out)
    reloaded = FileStateStore(tmp_path / ".conductor" / "state").get_state(payload["project_id"])

    assert resumed_exit == 1
    assert resumed_payload["released_stale_task_count"] == 1
    assert resumed_payload["manifest_verification"]["passed"] is True
    assert reloaded.task_assignments[0].status.value == "queued"
    assert reloaded.task_assignments[0].claim_reason == "resume cleanup"
    assert reloaded.workitems[0].status.value == "pending"


def test_run_project_preflight_only_can_skip_without_requirement(tmp_path, capsys) -> None:
    exit_code = run_project.main(
        [
            "--project-root",
            str(tmp_path),
            "--preflight-only",
            "--skip-preflight-gate",
        ]
    )

    payload = json.loads(capsys.readouterr().out)
    assert exit_code == 0
    assert payload == {
        "ok": True,
        "skipped": True,
        "reason": "skip_preflight_gate",
        "project_root": str(tmp_path),
        "preflight_gate": {
            "run_profile": "mock",
            "agent_cli": None,
            "llm_harness_backend": None,
        },
    }
    assert not (tmp_path / ".conductor" / "state").exists()


def test_run_project_preflight_only_blocks_real_profile_without_backend(monkeypatch, tmp_path, capsys) -> None:
    llm_runtime_config = LLMRuntimeConfig(
        local=LLMHTTPConfig(base_url="http://127.0.0.1:1234/v1", model_name="local-model", enabled=False),
        cloud=LLMHTTPConfig(base_url="https://example.com/v1", model_name="cloud-model", enabled=False),
        usage=LLMUsagePolicy(runner_enabled=False),
    )
    monkeypatch.setattr(run_project, "load_llm_runtime_config", lambda: llm_runtime_config)

    exit_code = run_project.main(
        [
            "--project-root",
            str(tmp_path),
            "--run-profile",
            "design_cli_only",
            "--preflight-only",
        ]
    )

    payload = json.loads(capsys.readouterr().out)
    assert exit_code == 2
    assert payload["ok"] is False
    assert payload["project_root"] == str(tmp_path)
    assert payload["preflight_gate"]["run_profile"] == "design_cli_only"
    assert payload["preflight_gate"]["agent_cli"] is None
    assert payload["preflight_gate"]["llm_harness_backend"] is None
    assert "requires real outputs" in payload["preflight_gate"]["errors"][0]
    assert not (tmp_path / ".conductor" / "state").exists()


def test_preflight_gate_blocks_real_profile_without_backend(tmp_path) -> None:
    run_profile = resolve_run_profile("design_cli_only")
    llm_runtime_config = LLMRuntimeConfig(
        local=LLMHTTPConfig(base_url="http://127.0.0.1:1234/v1", model_name="local-model", enabled=False),
        cloud=LLMHTTPConfig(base_url="https://example.com/v1", model_name="cloud-model", enabled=False),
        usage=LLMUsagePolicy(runner_enabled=False),
    )

    payload = _run_preflight_gate(
        cli_config=_build_cli_config(None, run_profile),
        llm_runtime_config=llm_runtime_config,
        project_root=tmp_path,
        run_profile=run_profile,
        agent_cli=None,
        llm_harness_backend=None,
    )

    assert payload["ok"] is False
    assert "requires real outputs" in payload["preflight_gate"]["errors"][0]
    assert payload["preflight_gate"]["recommendations"]
    diagnostics_path = tmp_path / ".conductor" / "diagnostics" / "run-preflight" / "preflight-gate.json"
    persisted = json.loads(diagnostics_path.read_text(encoding="utf-8"))
    assert persisted["ok"] is False
    assert persisted["preflight_gate"]["diagnostics_path"] == str(diagnostics_path)
    assert persisted["preflight_gate"]["recommendations"]
    assert payload["preflight_gate"]["diagnostics_path"] == str(diagnostics_path)


def test_preflight_gate_skips_mock_run_even_when_llm_runner_is_configured(tmp_path) -> None:
    run_profile = resolve_run_profile("mock")
    llm_runtime_config = LLMRuntimeConfig(
        local=LLMHTTPConfig(base_url="http://127.0.0.1:1234/v1", model_name="local-model", enabled=True),
        cloud=LLMHTTPConfig(base_url="https://example.com/v1", model_name="cloud-model", enabled=True),
        usage=LLMUsagePolicy(runner_enabled=True),
    )

    payload = _run_preflight_gate(
        cli_config=_build_cli_config(None, run_profile),
        llm_runtime_config=llm_runtime_config,
        project_root=tmp_path,
        run_profile=run_profile,
        agent_cli=None,
        llm_harness_backend=None,
    )

    assert payload == {"ok": True, "skipped": True, "reason": "mock_or_offline_run"}


def test_preflight_gate_allows_ready_llm_harness(monkeypatch, tmp_path) -> None:
    run_profile = resolve_run_profile("design_cli_only")
    llm_runtime_config = LLMRuntimeConfig(
        local=LLMHTTPConfig(base_url="http://127.0.0.1:1234/v1", model_name="local-model", enabled=True),
        cloud=LLMHTTPConfig(base_url="https://example.com/v1", model_name="cloud-model", enabled=False),
        usage=LLMUsagePolicy(runner_enabled=False),
    )

    monkeypatch.setattr(run_project, "build_platform_diagnostics", lambda **_: fake_diagnostics("local", "ready"))

    payload = _run_preflight_gate(
        cli_config=_build_cli_config(None, run_profile),
        llm_runtime_config=llm_runtime_config,
        project_root=tmp_path,
        run_profile=run_profile,
        agent_cli=None,
        llm_harness_backend="local",
    )

    assert payload["ok"] is True
    assert (tmp_path / ".conductor" / "diagnostics" / "run-preflight" / "preflight-gate.json").exists()


def test_preflight_gate_ignores_irrelevant_cloud_warning_when_local_harness_is_selected(monkeypatch, tmp_path) -> None:
    run_profile = resolve_run_profile("design_cli_only")
    llm_runtime_config = LLMRuntimeConfig(
        local=LLMHTTPConfig(base_url="http://127.0.0.1:1234/v1", model_name="local-model", enabled=True),
        cloud=LLMHTTPConfig(base_url="https://example.com/v1", model_name="cloud-model", enabled=True),
        usage=LLMUsagePolicy(runner_enabled=False),
    )

    monkeypatch.setattr(
        run_project,
        "build_platform_diagnostics",
        lambda **_: fake_diagnostics(
            "local",
            "ready",
            warnings=["Cloud LLM `cloud-model` is enabled but API key is missing."],
            extra_backends=[("cloud", "failed", "Fill the cloud API key")],
        ),
    )

    payload = _run_preflight_gate(
        cli_config=_build_cli_config(None, run_profile),
        llm_runtime_config=llm_runtime_config,
        project_root=tmp_path,
        run_profile=run_profile,
        agent_cli=None,
        llm_harness_backend="local",
    )

    assert payload["ok"] is True
    assert payload["preflight_gate"]["errors"] == []
    assert not any("API key" in item for item in payload["preflight_gate"]["recommendations"])


def test_preflight_gate_blocks_failed_llm_harness(monkeypatch, tmp_path) -> None:
    run_profile = resolve_run_profile("design_cli_only")
    llm_runtime_config = LLMRuntimeConfig(
        local=LLMHTTPConfig(base_url="http://127.0.0.1:1234/v1", model_name="local-model", enabled=True),
        cloud=LLMHTTPConfig(base_url="https://example.com/v1", model_name="cloud-model", enabled=False),
        usage=LLMUsagePolicy(runner_enabled=False),
    )

    monkeypatch.setattr(
        run_project,
        "build_platform_diagnostics",
        lambda **_: fake_diagnostics("local", "failed", warnings=["local LLM preflight failed"]),
    )

    payload = _run_preflight_gate(
        cli_config=_build_cli_config(None, run_profile),
        llm_runtime_config=llm_runtime_config,
        project_root=tmp_path,
        run_profile=run_profile,
        agent_cli=None,
        llm_harness_backend="local",
    )

    assert payload["ok"] is False
    assert any("preflight failed" in error for error in payload["preflight_gate"]["errors"])


def fake_diagnostics(
    backend: str,
    health_status: str,
    warnings: list[str] | None = None,
    extra_backends: list[tuple[str, str, str]] | None = None,
):
    backends = [
        SimpleNamespace(
            backend=backend,
            health_status=health_status,
            recommendation="diagnostic recommendation",
        )
    ]
    for item_backend, item_status, item_recommendation in extra_backends or []:
        backends.append(
            SimpleNamespace(
                backend=item_backend,
                health_status=item_status,
                recommendation=item_recommendation,
            )
        )
    return SimpleNamespace(
        warnings=warnings or [],
        llm_backends=backends,
        to_dict=lambda: {
            "llm_backends": [
                {"backend": item.backend, "health_status": item.health_status}
                for item in backends
            ]
        },
    )
