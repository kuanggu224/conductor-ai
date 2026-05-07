"""Run a Conductor project from UTF-8 requirement input."""

from __future__ import annotations

import argparse
import json
from dataclasses import asdict
from datetime import datetime
from pathlib import Path

from conductor.config.cli import CLISelectionConfig
from conductor.config.execution import RunProfile, resolve_run_profile
from conductor.config.llm import load_llm_runtime_config
from conductor.config.system import SystemConfig
from conductor.controller.engine import ConductorEngine
from conductor.diagnostics import build_platform_diagnostics, build_requirement_llm_preflight_probe
from conductor.io.encoding import configure_utf8_stdio
from conductor.io.requirements import load_requirement_text
from conductor.state.file_store import FileStateStore

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
        "--static-requirement-review",
        action="store_true",
        help="Disable dynamic requirement review seats for faster controlled smoke runs.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    project_root = _resolve_project_root(args.project_root, args.project_name)
    run_profile = resolve_run_profile(args.run_profile)
    agent_cli = _resolve_agent_cli(args.codex, args.agent_cli)
    if args.diagnose:
        cli_config = _build_cli_config(agent_cli, run_profile, aspirecode_model=args.aspirecode_model)
        llm_runtime_config = _build_llm_runtime_config(args)
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

    requirement = load_requirement_text(
        requirement=args.requirement,
        requirement_file=args.requirement_file,
        requirement_json_file=args.requirement_json_file,
        json_key=args.requirement_json_key,
    )
    cli_config = _build_cli_config(agent_cli, run_profile, aspirecode_model=args.aspirecode_model)
    llm_runtime_config = _build_llm_runtime_config(args)
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
    state = engine.create_project(requirement=requirement, project_root=str(project_root))
    state = engine.run_project(state.project.id, max_steps=args.max_steps)
    report_path = engine.write_project_report(state.project.id)
    manifest_path = engine.write_run_manifest(state.project.id, report_path=report_path)
    payload = {
        "project_id": state.project.id,
        "status": state.project_status.value,
        "current_stage": state.current_stage,
        "project_root": state.project.project_root,
        "run_profile": run_profile.profile.value,
        "report_path": str(report_path),
        "manifest_path": str(manifest_path),
        "workitems": [asdict(item) for item in state.workitems],
        "artifacts": [asdict(item) for item in state.artifacts],
    }
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0 if state.project_status.value == "completed" else 1


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


def _build_llm_runtime_config(args):
    runtime_config = load_llm_runtime_config()
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


def _build_system_config(args) -> SystemConfig:
    config = SystemConfig.load()
    if args.collaboration_max_rounds is not None:
        config.collaboration.max_rounds = args.collaboration_max_rounds
    if args.static_requirement_review:
        config.collaboration.dynamic_requirement_review_enabled = False
    return config

if __name__ == "__main__":
    raise SystemExit(main())
