"""Run a Conductor project from UTF-8 requirement input."""

from __future__ import annotations

import argparse
import hashlib
import json
from dataclasses import asdict
from datetime import datetime
from pathlib import Path

from conductor.audit_bundle import AUDIT_BUNDLE_SCHEMA_VERSION, verify_audit_bundle
from conductor.config.cli import CLISelectionConfig
from conductor.config.execution import RunProfile, resolve_run_profile
from conductor.config.llm import load_llm_runtime_config
from conductor.config.system import SystemConfig
from conductor.controller.engine import ConductorEngine
from conductor.diagnostics import build_platform_diagnostics, build_requirement_llm_preflight_probe
from conductor.execution_readiness import evaluate_execution_readiness
from conductor.io.encoding import configure_utf8_stdio
from conductor.io.requirements import load_requirement_text
from conductor.preflight_gate import write_preflight_gate_payload
from conductor.replay_trace import build_manifest_replay_trace
from conductor.replay_verifier import verify_manifest
from conductor.state.file_store import FileStateStore
from conductor.task_center.service import DEFAULT_STALE_CLAIMED_AFTER_SECONDS, TaskCenterService

configure_utf8_stdio()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m app.run_project")
    parser.add_argument("--requirement", default="", help="Direct requirement text. Prefer files for non-ASCII text.")
    parser.add_argument("--requirement-file", help="UTF-8 text file containing the requirement.")
    parser.add_argument("--requirement-json-file", help="UTF-8 JSON file containing the requirement.")
    parser.add_argument("--requirement-json-key", default="requirement", help="JSON key path, e.g. requirement or task.prompt.")
    parser.add_argument("--project-root", default=str(Path.cwd()), help="Project workspace directory.")
    parser.add_argument(
        "--project-name",
        help="Optional child folder name under --project-root. Useful when --project-root is a test workspace root.",
    )
    parser.add_argument("--max-steps", type=int, default=80)
    parser.add_argument(
        "--run-profile",
        choices=[profile.value for profile in RunProfile],
        default=RunProfile.MOCK.value,
        help="Execution mode: mock, design_cli_only, code_cli, or full_cli.",
    )
    parser.add_argument("--codex", action="store_true", help="Bind all preset agents to Codex CLI.")
    parser.add_argument(
        "--agent-cli",
        choices=["codex", "opencode", "aspirecode", "claude", "qwen", "aider", "gemini"],
        help="Bind all preset agents to the selected Agent CLI.",
    )
    parser.add_argument("--aspirecode-model", help="Model for AspireCode CLI, e.g. lmstudio-local/qwen3.6-35b-a3b.")
    parser.add_argument(
        "--llm-harness",
        choices=["local", "cloud"],
        help="Use the controlled LLMHarness with the selected OpenAI-compatible backend.",
    )
    parser.add_argument("--llm-base-url", help="Override the selected LLMHarness backend base URL.")
    parser.add_argument("--llm-model", help="Override the selected LLMHarness backend model.")
    parser.add_argument("--llm-api-key", help="Override the selected LLMHarness backend API key.")
    parser.add_argument("--llm-timeout", type=float, help="Override the selected LLMHarness backend timeout seconds.")
    parser.add_argument(
        "--llm-reasoning-effort",
        choices=["none", "low", "medium", "high"],
        help="Override OpenAI-compatible reasoning effort. For Qwen thinking models, use none for fast local runs.",
    )
    parser.add_argument("--diagnose", action="store_true", help="Print platform diagnostics and exit.")
    parser.add_argument(
        "--diagnose-cli",
        action="store_true",
        help="When used with --diagnose, run lightweight --version probes for Agent CLI tools.",
    )
    parser.add_argument(
        "--diagnose-llm",
        action="store_true",
        help="When used with --diagnose, also probe enabled OpenAI-compatible LLM servers.",
    )
    parser.add_argument(
        "--collaboration-max-rounds",
        type=int,
        help="Override requirement/design collaboration max rounds for this run.",
    )
    parser.add_argument(
        "--collaboration-kind",
        action="append",
        default=[],
        help=(
            "Enable multi-agent collaboration for an additional WorkItem kind in this run. "
            "Repeat to enable multiple kinds, e.g. ui_implementation and acceptance_check."
        ),
    )
    parser.add_argument(
        "--static-requirement-review",
        action="store_true",
        help="Disable dynamic requirement review seats for faster controlled smoke runs.",
    )
    parser.add_argument(
        "--skip-preflight-gate",
        action="store_true",
        help="Skip run preflight gate for controlled tests or intentionally offline runs.",
    )
    parser.add_argument(
        "--preflight-only",
        action="store_true",
        help="Run the current run-profile preflight gate and exit without creating a project.",
    )
    parser.add_argument(
        "--resume-project-id",
        help="Resume an existing project from --project-root/.conductor/state instead of creating a new project.",
    )
    parser.add_argument(
        "--release-stale-tasks",
        action="store_true",
        help="Before running, release stale claimed Task Center assignments back to queued.",
    )
    parser.add_argument(
        "--stale-after-seconds",
        type=int,
        default=DEFAULT_STALE_CLAIMED_AFTER_SECONDS,
        help="Stale claimed-task threshold used with --release-stale-tasks.",
    )
    parser.add_argument(
        "--stale-release-reason",
        default="resume stale cleanup",
        help="Release reason recorded when --release-stale-tasks requeues assignments.",
    )
    parser.add_argument(
        "--write-replay-trace",
        action="store_true",
        help="After writing the run manifest, also write a read-only replay trace.",
    )
    parser.add_argument(
        "--write-audit-bundle",
        action="store_true",
        help="After writing the run manifest, write both manifest verification and replay trace artifacts.",
    )
    parser.add_argument(
        "--audit-bundle-output",
        help="Optional audit bundle index output path. Defaults under .conductor/replay.",
    )
    parser.add_argument(
        "--audit-fail-on-warnings",
        action="store_true",
        help="When writing an audit bundle, return exit code 2 if audit bundle verification has warnings.",
    )
    parser.add_argument(
        "--write-manifest-verification",
        action="store_true",
        help="After writing the run manifest, also write the JSON manifest verification report.",
    )
    parser.add_argument(
        "--manifest-verification-output",
        help="Optional manifest verification output path. Defaults under .conductor/replay.",
    )
    parser.add_argument(
        "--replay-trace-format",
        choices=["json", "markdown"],
        default="markdown",
        help="Replay trace format used with --write-replay-trace.",
    )
    parser.add_argument(
        "--replay-trace-output",
        help="Optional replay trace output path. Defaults under .conductor/replay.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    project_root = _resolve_project_root(args.project_root, args.project_name)
    run_profile = resolve_run_profile(args.run_profile)
    agent_cli = _resolve_agent_cli(args.codex, args.agent_cli)
    if args.diagnose:
        cli_config = _build_cli_config(agent_cli, run_profile, aspirecode_model=args.aspirecode_model)
        llm_runtime_config = _build_llm_runtime_config(args, run_profile=run_profile)
        diagnostics = build_platform_diagnostics(
            cli_config=cli_config,
            project_root=project_root,
            llm_runtime_config=llm_runtime_config,
            probe_cli=args.diagnose_cli,
            probe_llm=args.diagnose_llm,
            preflight_probe=(
                build_requirement_llm_preflight_probe(
                    llm_runtime_config,
                    project_root / ".conductor" / "diagnostics",
                )
                if args.diagnose_llm
                else None
            ),
        )
        print(json.dumps(diagnostics.to_dict(), ensure_ascii=False, indent=2))
        return 0 if diagnostics.ok else 2

    cli_config = _build_cli_config(agent_cli, run_profile, aspirecode_model=args.aspirecode_model)
    llm_runtime_config = _build_llm_runtime_config(args, run_profile=run_profile)
    if args.preflight_only:
        if args.skip_preflight_gate:
            gate_payload = {"ok": True, "skipped": True, "reason": "skip_preflight_gate"}
        else:
            gate_payload = _run_preflight_gate(
                cli_config=cli_config,
                llm_runtime_config=llm_runtime_config,
                project_root=project_root,
                run_profile=run_profile,
                agent_cli=agent_cli,
                llm_harness_backend=args.llm_harness,
            )
        _add_preflight_output_metadata(
            gate_payload,
            project_root=project_root,
            run_profile=run_profile,
            agent_cli=agent_cli,
            llm_harness_backend=args.llm_harness,
        )
        print(json.dumps(gate_payload, ensure_ascii=False, indent=2))
        return 0 if gate_payload["ok"] is True else 2

    if not args.skip_preflight_gate:
        gate_payload = _run_preflight_gate(
            cli_config=cli_config,
            llm_runtime_config=llm_runtime_config,
            project_root=project_root,
            run_profile=run_profile,
            agent_cli=agent_cli,
            llm_harness_backend=args.llm_harness,
        )
        if gate_payload["ok"] is False:
            _add_preflight_output_metadata(
                gate_payload,
                project_root=project_root,
                run_profile=run_profile,
                agent_cli=agent_cli,
                llm_harness_backend=args.llm_harness,
            )
            print(json.dumps(gate_payload, ensure_ascii=False, indent=2))
            return 2

    engine = ConductorEngine(
        log_dir=project_root / ".conductor" / "logs",
        artifact_dir=project_root / ".conductor" / "artifacts",
        llm_runtime_config=llm_runtime_config,
        system_config=_build_system_config(args),
        state_store=FileStateStore(project_root / ".conductor" / "state"),
        cli_selection_config=cli_config,
        run_profile=run_profile.profile,
        require_real_design_outputs=run_profile.require_real_design_outputs,
        require_real_code_outputs=run_profile.require_real_code_outputs,
        llm_harness_backend=args.llm_harness,
    )
    if args.resume_project_id:
        state = engine.get_project(args.resume_project_id)
    else:
        requirement = load_requirement_text(
            requirement=args.requirement,
            requirement_file=args.requirement_file,
            requirement_json_file=args.requirement_json_file,
            json_key=args.requirement_json_key,
        )
        state = engine.create_project(requirement=requirement, project_root=str(project_root))
    released_stale_task_count = 0
    if args.release_stale_tasks:
        stale_release = TaskCenterService(engine.state_store, event_prefix="RunProject").release_stale(
            state.project.id,
            stale_after_seconds=args.stale_after_seconds,
            release_reason=args.stale_release_reason,
        )
        state = stale_release.state
        released_stale_task_count = len(stale_release.assignments)
    state = engine.run_project(state.project.id, max_steps=args.max_steps)
    report_path = engine.write_project_report(state.project.id)
    manifest_path = engine.write_run_manifest(state.project.id, report_path=report_path)
    manifest_verification = verify_manifest(manifest_path)
    manifest_verification_payload = _write_manifest_verification_if_requested(
        args,
        project_root,
        state.project.id,
        manifest_verification,
    )
    replay_trace_payload = _write_replay_trace_if_requested(args, project_root, manifest_path)
    audit_bundle_payload = _write_audit_bundle_index_if_requested(
        args,
        project_root,
        state.project.id,
        state.project_status.value,
        run_profile.profile.value,
        manifest_path,
        report_path,
        manifest_verification_payload,
        replay_trace_payload,
    )
    audit_bundle_verification_payload = _verify_audit_bundle_if_written(audit_bundle_payload)
    payload = {
        "project_id": state.project.id,
        "status": state.project_status.value,
        "current_stage": state.current_stage,
        "project_root": state.project.project_root,
        "run_profile": run_profile.profile.value,
        "resumed": bool(args.resume_project_id),
        "released_stale_task_count": released_stale_task_count,
        "report_path": str(report_path),
        "manifest_path": str(manifest_path),
        "manifest_verification": manifest_verification.to_dict(),
        "manifest_verification_report": manifest_verification_payload,
        "replay_trace": replay_trace_payload,
        "audit_bundle": audit_bundle_payload,
        "audit_bundle_verification": audit_bundle_verification_payload,
        "workitems": [asdict(item) for item in state.workitems],
        "artifacts": [asdict(item) for item in state.artifacts],
    }
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    if _audit_bundle_exit_failed(audit_bundle_verification_payload, fail_on_warnings=args.audit_fail_on_warnings):
        return 2
    return 0 if state.project_status.value == "completed" else 1


def _write_manifest_verification_if_requested(
    args,
    project_root: Path,
    project_id: str,
    manifest_verification,
) -> dict[str, object]:
    """Write an optional JSON manifest verification report after a project run."""
    if not (getattr(args, "write_manifest_verification", False) or getattr(args, "write_audit_bundle", False)):
        return {}
    output_path = (
        _resolve_project_output_path(project_root, args.manifest_verification_output)
        if getattr(args, "manifest_verification_output", None)
        else _default_manifest_verification_path(project_root, project_id)
    )
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(manifest_verification.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8")
    return {
        "path": str(output_path),
        "format": "json",
        "passed": manifest_verification.passed,
        "error_count": len(manifest_verification.errors),
        "warning_count": len(manifest_verification.warnings),
    }


def _default_manifest_verification_path(project_root: Path, project_id: str) -> Path:
    return project_root / ".conductor" / "replay" / f"{project_id}.verification.json"


def _write_audit_bundle_index_if_requested(
    args,
    project_root: Path,
    project_id: str,
    project_status: str,
    run_profile: str,
    manifest_path: Path,
    report_path: Path,
    manifest_verification_payload: dict[str, object],
    replay_trace_payload: dict[str, object],
) -> dict[str, object]:
    """Write an optional audit bundle index for downstream tooling."""
    if not getattr(args, "write_audit_bundle", False):
        return {}
    output_path = (
        _resolve_project_output_path(project_root, args.audit_bundle_output)
        if getattr(args, "audit_bundle_output", None)
        else _default_audit_bundle_path(project_root, project_id)
    )
    output_path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "schema_version": AUDIT_BUNDLE_SCHEMA_VERSION,
        "project_id": project_id,
        "status": project_status,
        "run_profile": run_profile,
        "generated_at": datetime.now().isoformat(),
        "files": {
            "manifest": str(manifest_path),
            "report": str(report_path),
            "manifest_verification": str(manifest_verification_payload.get("path", "")),
            "replay_trace": str(replay_trace_payload.get("path", "")),
        },
        "checksums": {
            "manifest": _sha256_file(manifest_path),
            "report": _sha256_file(report_path),
            "manifest_verification": _sha256_file(Path(str(manifest_verification_payload.get("path", "")))),
            "replay_trace": _sha256_file(Path(str(replay_trace_payload.get("path", "")))),
        },
        "summary": {
            "manifest_verification_passed": bool(manifest_verification_payload.get("passed")),
            "replay_trace_passed": bool(replay_trace_payload.get("passed")),
        },
    }
    output_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return {
        "path": str(output_path),
        "format": "json",
        "manifest_verification_passed": payload["summary"]["manifest_verification_passed"],
        "replay_trace_passed": payload["summary"]["replay_trace_passed"],
    }


def _default_audit_bundle_path(project_root: Path, project_id: str) -> Path:
    return project_root / ".conductor" / "replay" / f"{project_id}.audit.json"


def _verify_audit_bundle_if_written(audit_bundle_payload: dict[str, object]) -> dict[str, object]:
    bundle_path = str(audit_bundle_payload.get("path", ""))
    if not bundle_path:
        return {}
    return verify_audit_bundle(bundle_path).to_dict()


def _audit_bundle_exit_failed(audit_bundle_verification_payload: dict[str, object], *, fail_on_warnings: bool) -> bool:
    if not audit_bundle_verification_payload:
        return False
    if audit_bundle_verification_payload.get("passed") is not True:
        return True
    return fail_on_warnings and int(audit_bundle_verification_payload.get("warning_count", 0)) > 0


def _sha256_file(path: Path) -> str:
    if not path.exists() or not path.is_file():
        return ""
    digest = hashlib.sha256()
    with path.open("rb") as file:
        for chunk in iter(lambda: file.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _resolve_agent_cli(use_codex: bool, agent_cli: str | None) -> str | None:
    if agent_cli:
        return agent_cli
    if use_codex:
        return "codex"
    return None


def _resolve_project_root(project_root: str, project_name: str | None = None) -> Path:
    """Resolve the concrete per-run project directory.

    `C:\\99_self\\conductor_test` is treated as a workspace root, not a project
    root. This prevents generated files, logs, and node_modules from being
    mixed directly into the shared test container directory.
    """
    root = Path(project_root).expanduser().resolve()
    if project_name:
        return (root / project_name).resolve()
    if root.name.lower() == "conductor_test":
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        return (root / f"project_{timestamp}").resolve()
    return root


def _write_replay_trace_if_requested(args, project_root: Path, manifest_path: Path) -> dict[str, object]:
    """Write an optional replay trace artifact after a project run."""
    if not (getattr(args, "write_replay_trace", False) or getattr(args, "write_audit_bundle", False)):
        return {}
    trace = build_manifest_replay_trace(manifest_path)
    output_path = (
        _resolve_project_output_path(project_root, args.replay_trace_output)
        if getattr(args, "replay_trace_output", None)
        else _default_replay_trace_path(project_root, trace.project_id, args.replay_trace_format)
    )
    output_path.parent.mkdir(parents=True, exist_ok=True)
    content = trace.to_markdown() if args.replay_trace_format == "markdown" else json.dumps(trace.to_dict(), ensure_ascii=False, indent=2)
    output_path.write_text(content, encoding="utf-8")
    return {
        "path": str(output_path),
        "format": args.replay_trace_format,
        "passed": trace.passed,
        "event_count": len(trace.events),
    }


def _default_replay_trace_path(project_root: Path, project_id: str, output_format: str) -> Path:
    suffix = "md" if output_format == "markdown" else "json"
    return project_root / ".conductor" / "replay" / f"{project_id}.replay.{suffix}"


def _resolve_project_output_path(project_root: Path, output_path: str) -> Path:
    path = Path(output_path).expanduser()
    if path.is_absolute():
        return path.resolve()
    return (project_root / path).resolve()


def _build_cli_config(agent_cli: str | None, run_profile, aspirecode_model: str | None = None) -> CLISelectionConfig:
    roles = [
        "designer",
        "requirement_designer",
        "solution_designer",
        "backend_engineer",
        "frontend_engineer",
        "tester",
    ]
    selected_cli_names = [agent_cli] if agent_cli and run_profile.cli_roles else []
    return CLISelectionConfig(
        selected_cli_names=selected_cli_names,
        role_cli_bindings={
            role: agent_cli if agent_cli and run_profile.uses_cli_for_role(role) else None
            for role in roles
        },
        codex_model="gpt-5.4-mini",
        codex_reasoning_effort="medium",
        aspirecode_model=aspirecode_model or "lmstudio-local/qwen3.6-35b-a3b",
    )


def _build_llm_runtime_config(args, *, run_profile=None):
    runtime_config = load_llm_runtime_config()
    if run_profile is not None and run_profile.profile == RunProfile.MOCK and not args.llm_harness:
        runtime_config.usage.runner_enabled = False
    if not args.llm_harness:
        return runtime_config
    selected = runtime_config.local if args.llm_harness == "local" else runtime_config.cloud
    selected.enabled = True
    if args.llm_base_url:
        selected.base_url = args.llm_base_url
    if args.llm_model:
        selected.model_name = args.llm_model
    if args.llm_api_key:
        selected.api_key = args.llm_api_key
    if args.llm_timeout is not None:
        selected.timeout_seconds = args.llm_timeout
    if args.llm_reasoning_effort is not None:
        selected.reasoning_effort = args.llm_reasoning_effort
    return runtime_config


def _add_preflight_output_metadata(
    payload: dict[str, object],
    *,
    project_root: Path,
    run_profile,
    agent_cli: str | None,
    llm_harness_backend: str | None,
) -> None:
    """Stabilize preflight CLI JSON for scripts and human audit logs."""
    payload.setdefault("project_root", str(project_root))
    gate_payload = payload.setdefault("preflight_gate", {})
    if isinstance(gate_payload, dict):
        gate_payload.setdefault("run_profile", run_profile.profile.value)
        gate_payload.setdefault("agent_cli", agent_cli)
        gate_payload.setdefault("llm_harness_backend", llm_harness_backend)


def _run_preflight_gate(
    *,
    cli_config: CLISelectionConfig,
    llm_runtime_config,
    project_root: Path,
    run_profile,
    agent_cli: str | None,
    llm_harness_backend: str | None,
) -> dict[str, object]:
    """Fail fast when the selected real execution backend is clearly unusable."""
    requires_real_backend = run_profile.require_real_design_outputs or run_profile.require_real_code_outputs
    should_probe_cli = bool(agent_cli and run_profile.cli_roles)
    should_probe_llm = bool(llm_harness_backend or (requires_real_backend and llm_runtime_config.usage.runner_enabled))
    if not requires_real_backend and not should_probe_cli and not should_probe_llm:
        return {"ok": True, "skipped": True, "reason": "mock_or_offline_run"}

    diagnostics = build_platform_diagnostics(
        cli_config=cli_config,
        project_root=project_root,
        llm_runtime_config=llm_runtime_config,
        probe_cli=should_probe_cli,
        probe_llm=should_probe_llm,
        preflight_probe=(
            build_requirement_llm_preflight_probe(
                llm_runtime_config,
                project_root / ".conductor" / "diagnostics" / "run-preflight",
            )
            if should_probe_llm
            else None
        ),
    )
    errors = _preflight_gate_errors(
        diagnostics=diagnostics,
        run_profile=run_profile,
        agent_cli=agent_cli,
        llm_harness_backend=llm_harness_backend,
        runner_enabled=llm_runtime_config.usage.runner_enabled,
    )
    recommendations = _preflight_gate_recommendations(
        diagnostics=diagnostics,
        errors=errors,
        agent_cli=agent_cli,
        llm_harness_backend=llm_harness_backend,
        runner_enabled=llm_runtime_config.usage.runner_enabled,
    )
    execution_readiness = evaluate_execution_readiness(
        diagnostics=diagnostics,
        errors=errors,
        recommendations=recommendations,
        agent_cli=agent_cli,
        llm_harness_backend=llm_harness_backend,
        runner_enabled=llm_runtime_config.usage.runner_enabled,
    )
    payload = {
        "ok": not errors,
        "execution_readiness": execution_readiness.to_dict(),
        "preflight_gate": {
            "errors": errors,
            "recommendations": recommendations,
            "run_profile": run_profile.profile.value,
            "agent_cli": agent_cli,
            "llm_harness_backend": llm_harness_backend,
        },
        "diagnostics": diagnostics.to_dict(),
    }
    write_preflight_gate_payload(project_root, payload)
    return payload


def _preflight_gate_errors(
    *,
    diagnostics,
    run_profile,
    agent_cli: str | None,
    llm_harness_backend: str | None,
    runner_enabled: bool,
) -> list[str]:
    """Return user-facing gate errors for the selected execution mode."""
    errors: list[str] = []
    requires_real_backend = run_profile.require_real_design_outputs or run_profile.require_real_code_outputs
    if requires_real_backend and not agent_cli and not llm_harness_backend and not runner_enabled:
        errors.append(
            "Run profile requires real outputs, but no Agent CLI, LLMHarness backend, or LLM runner is configured."
        )
    errors.extend(
        _preflight_gate_relevant_warnings(
            diagnostics=diagnostics,
            agent_cli=agent_cli,
            llm_harness_backend=llm_harness_backend,
            runner_enabled=runner_enabled,
        )
    )
    if llm_harness_backend:
        backend = next(
            (item for item in diagnostics.llm_backends if item.backend == llm_harness_backend),
            None,
        )
        if backend is None:
            errors.append(f"LLMHarness backend `{llm_harness_backend}` is not present in diagnostics.")
        elif backend.health_status not in {"ready", "reachable", "models_unavailable"}:
            errors.append(
                f"LLMHarness backend `{llm_harness_backend}` is not ready: {backend.recommendation}"
            )
    return list(dict.fromkeys(error for error in errors if error))


def _preflight_gate_recommendations(
    *,
    diagnostics,
    errors: list[str],
    agent_cli: str | None,
    llm_harness_backend: str | None,
    runner_enabled: bool,
) -> list[str]:
    """Return concise next actions for failed or risky preflight gates."""
    recommendations: list[str] = []
    if not errors:
        return recommendations
    if agent_cli:
        recommendations.append("Run `python -m app.diagnostics --probe-cli` to verify the selected Agent CLI.")
    else:
        recommendations.append("Bind a real Agent CLI with `--agent-cli` or use `--llm-harness local|cloud`.")
    if llm_harness_backend:
        recommendations.append("Run `python -m app.diagnostics --preflight-llm` and verify base URL, model, key, and quota.")
    for backend in getattr(diagnostics, "llm_backends", []):
        if llm_harness_backend and getattr(backend, "backend", "") != llm_harness_backend:
            continue
        recommendation = getattr(backend, "recommendation", "")
        health_status = getattr(backend, "health_status", "")
        if recommendation and health_status in {"failed", "warning", "unreadable"}:
            recommendations.append(recommendation)
    for warning in _preflight_gate_relevant_warnings(
        diagnostics=diagnostics,
        agent_cli=agent_cli,
        llm_harness_backend=llm_harness_backend,
        runner_enabled=runner_enabled,
    ):
        if "API key is missing" in warning:
            recommendations.append("Fill the missing API key in `.conductor/llm.config.json` or the Board LLM settings page.")
        if "not available on PATH" in warning:
            recommendations.append("Install the selected CLI or remove it from the selected CLI bindings.")
    return list(dict.fromkeys(item for item in recommendations if item))


def _preflight_gate_relevant_warnings(
    *,
    diagnostics,
    agent_cli: str | None,
    llm_harness_backend: str | None,
    runner_enabled: bool,
) -> list[str]:
    """Filter diagnostics warnings to the backend selected for this run."""
    warnings = list(getattr(diagnostics, "warnings", []))
    if runner_enabled and not llm_harness_backend:
        return warnings
    relevant: list[str] = []
    for warning in warnings:
        if agent_cli and (f"`{agent_cli}`" in warning or f" {agent_cli}" in warning):
            relevant.append(warning)
            continue
        if llm_harness_backend and warning.startswith(f"{llm_harness_backend} LLM"):
            relevant.append(warning)
            continue
        if llm_harness_backend == "cloud" and "Cloud LLM" in warning:
            relevant.append(warning)
    return relevant


def _build_system_config(args) -> SystemConfig:
    config = SystemConfig.load()
    if args.collaboration_max_rounds is not None:
        config.collaboration.max_rounds = args.collaboration_max_rounds
    for kind in args.collaboration_kind:
        normalized = str(kind).strip()
        if normalized:
            config.collaboration.enabled_kinds.add(normalized)
    if args.static_requirement_review:
        config.collaboration.dynamic_requirement_review_enabled = False
    return config

if __name__ == "__main__":
    raise SystemExit(main())
