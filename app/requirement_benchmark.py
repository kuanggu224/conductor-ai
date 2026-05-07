"""CLI for requirement-stage scoring and platform-vs-direct comparison."""

from __future__ import annotations

import argparse
import json
from dataclasses import asdict
from pathlib import Path

from conductor.io.encoding import configure_utf8_stdio
from conductor.config.llm import load_llm_runtime_config
from conductor.requirement_benchmark import (
    compare_requirement_documents,
    default_requirement_benchmark_cases,
    evaluate_requirement_artifact_from_manifest,
    evaluate_requirement_document,
    extract_requirement_document_from_manifest,
    run_direct_requirement_baseline,
    run_requirement_llm_preflight,
    write_requirement_comparison_report,
)

configure_utf8_stdio()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m app.requirement_benchmark")
    subparsers = parser.add_subparsers(dest="command", required=True)

    score = subparsers.add_parser("score", help="Score one requirement document or Conductor manifest.")
    score.add_argument("--case", required=True, help="Requirement benchmark case id.")
    score.add_argument("--document-file", help="Markdown/text requirement document to score.")
    score.add_argument("--manifest", help="Conductor run manifest to score.")
    score.add_argument("--min-score", type=int, default=70)

    compare = subparsers.add_parser("compare", help="Compare Conductor requirement output with direct model output.")
    compare.add_argument("--case", required=True, help="Requirement benchmark case id.")
    compare.add_argument("--platform-file", help="Platform requirement document file.")
    compare.add_argument("--platform-manifest", help="Conductor run manifest containing platform artifacts.")
    compare.add_argument("--direct-file", help="Direct model output document file.")
    compare.add_argument(
        "--direct-llm",
        choices=["local", "cloud"],
        help="Generate direct baseline with the selected configured LLM backend.",
    )
    compare.add_argument(
        "--direct-prompt-mode",
        choices=["plain", "structured"],
        default="plain",
        help="Prompt style for --direct-llm. plain is the default direct-model baseline.",
    )
    compare.add_argument("--output-dir", default=str(Path(".conductor_benchmarks") / "requirements"))
    compare.add_argument("--min-score", type=int, default=70)
    compare.add_argument("--min-delta", type=int, default=5)

    suite = subparsers.add_parser("suite", help="Compare a batch of Conductor requirement outputs with baselines.")
    suite.add_argument(
        "--cases",
        nargs="*",
        help="Case ids to run. Defaults to all fixed requirement benchmark cases.",
    )
    suite.add_argument(
        "--platform-dir",
        required=True,
        help="Directory containing <case>.platform.md, <case>.md, or manifest files.",
    )
    suite.add_argument(
        "--direct-dir",
        help="Directory containing <case>.direct-requirement.md, <case>.direct.md, or <case>.md.",
    )
    suite.add_argument(
        "--direct-llm",
        choices=["local", "cloud"],
        help="Generate direct baselines for every selected case with the configured LLM backend.",
    )
    suite.add_argument("--output-dir", default=str(Path(".conductor_benchmarks") / "requirements"))
    suite.add_argument("--min-score", type=int, default=70)
    suite.add_argument("--min-delta", type=int, default=5)

    run_suite = subparsers.add_parser(
        "run-suite",
        help="Generate Conductor requirement outputs, compare them with baselines, and write a suite report.",
    )
    run_suite.add_argument(
        "--cases",
        nargs="*",
        help="Case ids to run. Defaults to all fixed requirement benchmark cases.",
    )
    run_suite.add_argument("--output-dir", default=str(Path(".conductor_benchmarks") / "requirements-e2e"))
    run_suite.add_argument(
        "--run-profile",
        choices=["mock", "design_cli_only", "code_cli", "full_cli"],
        default="mock",
        help="Conductor run profile used for platform requirement generation.",
    )
    run_suite.add_argument(
        "--platform-llm",
        choices=["local", "cloud"],
        help="Use LLMHarness for Conductor requirement-stage generation and review.",
    )
    run_suite.add_argument("--max-steps", type=int, default=20)
    run_suite.add_argument(
        "--collaboration-max-rounds",
        type=int,
        help="Override requirement/design collaboration max rounds for platform generation.",
    )
    run_suite.add_argument(
        "--static-requirement-review",
        action="store_true",
        help="Disable dynamic requirement review seats for faster controlled benchmark runs.",
    )
    run_suite.add_argument(
        "--direct-dir",
        help="Directory containing pre-generated direct baseline files.",
    )
    run_suite.add_argument(
        "--direct-llm",
        choices=["local", "cloud"],
        help="Generate direct baselines for every selected case with the configured LLM backend.",
    )
    run_suite.add_argument(
        "--direct-prompt-mode",
        choices=["plain", "structured"],
        default="plain",
        help="Prompt style for --direct-llm. plain is the default direct-model baseline.",
    )
    run_suite.add_argument("--min-score", type=int, default=70)
    run_suite.add_argument("--min-delta", type=int, default=5)
    run_suite.add_argument(
        "--skip-preflight",
        action="store_true",
        help="Skip LLM connectivity preflight before generated suite execution.",
    )
    _add_llm_override_args(run_suite)

    preflight = subparsers.add_parser("preflight", help="Check a configured LLM backend for requirement benchmarks.")
    preflight.add_argument("--backend", choices=["local", "cloud"], required=True)
    preflight.add_argument("--output-dir", default=str(Path(".conductor_benchmarks") / "requirements-preflight"))
    _add_llm_override_args(preflight)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.command == "suite":
        return _run_suite(args)
    if args.command == "run-suite":
        return _run_generated_suite(args)
    if args.command == "preflight":
        return _run_preflight(args)

    case = _case_by_id(args.case)
    if args.command == "score":
        if bool(args.document_file) == bool(args.manifest):
            raise SystemExit("Pass exactly one of --document-file or --manifest.")
        if args.manifest:
            evaluation = evaluate_requirement_artifact_from_manifest(args.manifest, case, min_score=args.min_score)
        else:
            document = Path(args.document_file).read_text(encoding="utf-8")
            evaluation = evaluate_requirement_document(document, case, min_score=args.min_score)
        print(json.dumps(asdict(evaluation), ensure_ascii=False, indent=2))
        return 0 if evaluation.passed else 1

    if bool(args.platform_file) == bool(args.platform_manifest):
        raise SystemExit("Pass exactly one of --platform-file or --platform-manifest.")
    platform_document = (
        extract_requirement_document_from_manifest(args.platform_manifest)
        if args.platform_manifest
        else Path(args.platform_file).read_text(encoding="utf-8")
    )
    if bool(args.direct_file) == bool(args.direct_llm):
        raise SystemExit("Pass exactly one of --direct-file or --direct-llm.")
    if args.direct_llm:
        runtime_config = load_llm_runtime_config()
        llm_config = runtime_config.local if args.direct_llm == "local" else runtime_config.cloud
        llm_config.enabled = True
        direct = run_direct_requirement_baseline(
            case,
            output_dir=Path(args.output_dir) / "direct",
            config=llm_config,
            prompt_mode=args.direct_prompt_mode,
        )
        direct_document = direct.content
    else:
        direct_document = Path(args.direct_file).read_text(encoding="utf-8")
    comparison = compare_requirement_documents(
        case=case,
        platform_document=platform_document,
        direct_document=direct_document,
        min_score=args.min_score,
        min_delta=args.min_delta,
    )
    result = write_requirement_comparison_report([comparison], args.output_dir)
    print(json.dumps(asdict(result), ensure_ascii=False, indent=2))
    return 0 if comparison.passed else 1


def _run_suite(args) -> int:
    cases = [_case_by_id(case_id) for case_id in args.cases] if args.cases else default_requirement_benchmark_cases()
    if bool(args.direct_dir) == bool(args.direct_llm):
        raise SystemExit("Pass exactly one of --direct-dir or --direct-llm.")

    runtime_config = load_llm_runtime_config() if args.direct_llm else None
    comparisons = []
    for case in cases:
        platform_document = _read_platform_case_document(Path(args.platform_dir), case.id)
        if args.direct_llm:
            assert runtime_config is not None
            llm_config = runtime_config.local if args.direct_llm == "local" else runtime_config.cloud
            llm_config.enabled = True
            direct = run_direct_requirement_baseline(
                case,
                output_dir=Path(args.output_dir) / "direct",
                config=llm_config,
            )
            direct_document = direct.content
        else:
            direct_document = _read_direct_case_document(Path(args.direct_dir), case.id)
        comparisons.append(
            compare_requirement_documents(
                case=case,
                platform_document=platform_document,
                direct_document=direct_document,
                min_score=args.min_score,
                min_delta=args.min_delta,
            )
        )

    result = write_requirement_comparison_report(comparisons, args.output_dir)
    print(json.dumps(asdict(result), ensure_ascii=False, indent=2))
    return 0 if all(comparison.passed for comparison in comparisons) else 1


def _run_generated_suite(args) -> int:
    cases = [_case_by_id(case_id) for case_id in args.cases] if args.cases else default_requirement_benchmark_cases()
    if bool(args.direct_dir) == bool(args.direct_llm):
        raise SystemExit("Pass exactly one of --direct-dir or --direct-llm.")

    output_dir = Path(args.output_dir).expanduser().resolve()
    runtime_config = load_llm_runtime_config() if args.direct_llm else None
    if not args.skip_preflight:
        _preflight_generated_suite_backends(args, output_dir=output_dir)
    comparisons = []
    platform_runs = []
    direct_runs = []
    for case in cases:
        try:
            platform = _run_platform_requirement_case(
                case=case,
                output_dir=output_dir / "platform",
                run_profile=args.run_profile,
                max_steps=args.max_steps,
                platform_llm=args.platform_llm,
                llm_overrides=_llm_overrides_from_args(args),
                collaboration_max_rounds=args.collaboration_max_rounds,
                dynamic_requirement_review_enabled=not args.static_requirement_review,
            )
        except Exception as error:
            raise SystemExit(f"Platform requirement generation failed for {case.id}: {error}") from None
        platform_runs.append({key: value for key, value in platform.items() if key != "document"})
        if args.direct_llm:
            assert runtime_config is not None
            llm_config = runtime_config.local if args.direct_llm == "local" else runtime_config.cloud
            llm_config.enabled = True
            _apply_llm_overrides(llm_config, args)
            try:
                direct = run_direct_requirement_baseline(
                    case,
                    output_dir=output_dir / "direct",
                    config=llm_config,
                    prompt_mode=args.direct_prompt_mode,
                )
            except Exception as error:
                raise SystemExit(f"Direct requirement baseline failed for {case.id}: {error}") from None
            direct_document = direct.content
            direct_runs.append(
                {
                    "case_id": case.id,
                    "source": "llm",
                    "backend": args.direct_llm,
                    "model": direct.model,
                    "prompt_mode": args.direct_prompt_mode,
                    "duration_ms": direct.duration_ms,
                    "output_path": direct.output_path,
                }
            )
        else:
            try:
                direct_path = _resolve_direct_case_document(Path(args.direct_dir), case.id)
                direct_document = direct_path.read_text(encoding="utf-8")
            except Exception as error:
                raise SystemExit(f"Direct baseline file failed for {case.id}: {error}") from None
            direct_runs.append(
                {
                    "case_id": case.id,
                    "source": "file",
                    "backend": "",
                    "model": "",
                    "duration_ms": 0,
                    "output_path": str(direct_path),
                }
            )
        comparisons.append(
            compare_requirement_documents(
                case=case,
                platform_document=platform["document"],
                direct_document=direct_document,
                min_score=args.min_score,
                min_delta=args.min_delta,
            )
        )

    result = write_requirement_comparison_report(comparisons, output_dir)
    payload = asdict(result)
    payload["platform_runs"] = platform_runs
    payload["direct_runs"] = direct_runs
    generated_path = output_dir / "requirement-generated-suite.json"
    generated_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    payload["generated_suite_path"] = str(generated_path)
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0 if all(comparison.passed for comparison in comparisons) else 1


def _run_preflight(args) -> int:
    runtime_config = load_llm_runtime_config()
    llm_config = runtime_config.local if args.backend == "local" else runtime_config.cloud
    llm_config.enabled = True
    _apply_llm_overrides(llm_config, args)
    result = run_requirement_llm_preflight(
        backend=args.backend,
        config=llm_config,
        output_dir=Path(args.output_dir),
    )
    print(json.dumps(asdict(result), ensure_ascii=False, indent=2))
    return 0 if result.success else 1


def _preflight_generated_suite_backends(args, *, output_dir: Path) -> None:
    backends = []
    if args.platform_llm:
        backends.append(("platform", args.platform_llm))
    if args.direct_llm:
        backends.append(("direct", args.direct_llm))
    if not backends:
        return
    runtime_config = load_llm_runtime_config()
    checked: set[str] = set()
    for purpose, backend in backends:
        if backend in checked:
            continue
        checked.add(backend)
        llm_config = runtime_config.local if backend == "local" else runtime_config.cloud
        llm_config.enabled = True
        _apply_llm_overrides(llm_config, args)
        result = run_requirement_llm_preflight(
            backend=backend,
            config=llm_config,
            output_dir=output_dir / "preflight",
        )
        if not result.success:
            raise SystemExit(
                f"{purpose} LLM preflight failed for {backend}: "
                f"{result.error or 'empty response'} "
                f"(base_url={result.base_url}, model={result.model})"
            )


def _run_platform_requirement_case(
    *,
    case,
    output_dir: Path,
    run_profile: str,
    max_steps: int,
    platform_llm: str | None,
    llm_overrides: dict[str, str | float | None] | None = None,
    collaboration_max_rounds: int | None = None,
    dynamic_requirement_review_enabled: bool = True,
) -> dict[str, str | int]:
    from conductor.config.cli import CLISelectionConfig
    from conductor.config.execution import resolve_run_profile
    from conductor.config.llm import load_llm_runtime_config
    from conductor.config.system import SystemConfig
    from conductor.controller.engine import ConductorEngine
    from conductor.state.file_store import FileStateStore

    output_dir = output_dir.expanduser().resolve()
    project_root = output_dir / case.id
    project_root.mkdir(parents=True, exist_ok=True)
    resolved_profile = resolve_run_profile(run_profile)
    llm_runtime_config = load_llm_runtime_config()
    if platform_llm:
        selected = llm_runtime_config.local if platform_llm == "local" else llm_runtime_config.cloud
        selected.enabled = True
        _apply_llm_override_values(selected, llm_overrides or {})
    system_config = SystemConfig.load()
    if collaboration_max_rounds is not None:
        system_config.collaboration.max_rounds = collaboration_max_rounds
    system_config.collaboration.dynamic_requirement_review_enabled = dynamic_requirement_review_enabled
    engine = ConductorEngine(
        log_dir=project_root / ".conductor" / "logs",
        artifact_dir=project_root / ".conductor" / "artifacts",
        llm_runtime_config=llm_runtime_config,
        system_config=system_config,
        state_store=FileStateStore(project_root / ".conductor" / "state"),
        cli_selection_config=CLISelectionConfig(),
        run_profile=resolved_profile.profile,
        require_real_design_outputs=resolved_profile.require_real_design_outputs,
        require_real_code_outputs=False,
        llm_harness_backend=platform_llm,
    )
    state = engine.create_project(case.requirement, project_root=str(project_root))
    steps = 0
    while state.current_stage == "requirement" and steps < max_steps and not engine.is_terminal(state):
        state = engine.step_project(state.project.id)
        steps += 1
    report_path = engine.write_project_report(state.project.id)
    manifest_path = engine.write_run_manifest(state.project.id, report_path)
    document = extract_requirement_document_from_manifest(manifest_path)
    return {
        "case_id": case.id,
        "project_id": state.project.id,
        "project_root": str(project_root),
        "manifest_path": str(manifest_path),
        "report_path": str(report_path),
        "current_stage": state.current_stage or "",
        "status": state.project_status.value,
        "steps": steps,
        "document": document,
    }


def _read_platform_case_document(root: Path, case_id: str) -> str:
    root = root.expanduser().resolve()
    for path in _platform_case_candidates(root, case_id):
        if not path.exists():
            continue
        if path.suffix.lower() == ".json":
            return extract_requirement_document_from_manifest(path)
        return path.read_text(encoding="utf-8")
    raise FileNotFoundError(f"No platform requirement document or manifest found for case: {case_id}")


def _read_direct_case_document(root: Path, case_id: str) -> str:
    return _resolve_direct_case_document(root, case_id).read_text(encoding="utf-8")


def _resolve_direct_case_document(root: Path, case_id: str) -> Path:
    root = root.expanduser().resolve()
    for path in [
        root / f"{case_id}.direct-requirement.md",
        root / f"{case_id}.direct.md",
        root / f"{case_id}.md",
    ]:
        if path.exists():
            return path
    raise FileNotFoundError(f"No direct baseline document found for case: {case_id}")


def _platform_case_candidates(root: Path, case_id: str) -> list[Path]:
    candidates = [
        root / f"{case_id}.platform.md",
        root / f"{case_id}.md",
        root / f"{case_id}.manifest.json",
        root / case_id / f"{case_id}.manifest.json",
    ]
    manifest_dir = root / case_id / ".conductor" / "manifests"
    if manifest_dir.exists():
        candidates.extend(sorted(manifest_dir.glob("*.manifest.json"), key=lambda path: path.stat().st_mtime, reverse=True))
    return candidates


def _add_llm_override_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--llm-base-url", help="Override selected LLM backend base URL.")
    parser.add_argument("--llm-model", help="Override selected LLM backend model.")
    parser.add_argument("--llm-api-key", help="Override selected LLM backend API key.")
    parser.add_argument("--llm-timeout", type=float, help="Override selected LLM backend timeout seconds.")
    parser.add_argument(
        "--llm-reasoning-effort",
        choices=["none", "low", "medium", "high"],
        help="Override OpenAI-compatible reasoning effort.",
    )


def _llm_overrides_from_args(args) -> dict[str, str | float | None]:
    return {
        "base_url": args.llm_base_url,
        "model_name": args.llm_model,
        "api_key": args.llm_api_key,
        "timeout_seconds": args.llm_timeout,
        "reasoning_effort": args.llm_reasoning_effort,
    }


def _apply_llm_overrides(config, args) -> None:
    _apply_llm_override_values(config, _llm_overrides_from_args(args))


def _apply_llm_override_values(config, overrides: dict[str, str | float | None]) -> None:
    for key, value in overrides.items():
        if value is not None:
            setattr(config, key, value)


def _case_by_id(case_id: str):
    for case in default_requirement_benchmark_cases():
        if case.id == case_id:
            return case
    known = ", ".join(case.id for case in default_requirement_benchmark_cases())
    raise SystemExit(f"Unknown requirement benchmark case: {case_id}. Known cases: {known}")


if __name__ == "__main__":
    raise SystemExit(main())
