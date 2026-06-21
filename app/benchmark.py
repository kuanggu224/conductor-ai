"""Run Conductor benchmark suites."""

from __future__ import annotations

import argparse
import json
from dataclasses import asdict
from pathlib import Path

from conductor.benchmark import BenchmarkRunner, default_benchmark_cases
from conductor.config.execution import RunProfile
from conductor.io.encoding import configure_utf8_stdio

configure_utf8_stdio()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m app.benchmark")
    parser.add_argument("--output-dir", default=str(Path(".conductor_benchmarks")), help="Directory for benchmark outputs.")
    parser.add_argument("--case", default="all", help="Benchmark case id, or all.")
    parser.add_argument(
        "--profiles",
        default=RunProfile.MOCK.value,
        help="Comma-separated run profiles, e.g. mock,design_cli_only.",
    )
    parser.add_argument("--codex", action="store_true", help="Use Codex CLI for CLI-enabled profiles.")
    parser.add_argument("--max-steps", type=int, default=80)
    parser.add_argument(
        "--quality-comparison",
        action="store_true",
        help="Run single-agent versus multi-agent quality comparison for the selected cases.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    cases = default_benchmark_cases()
    if args.case != "all":
        cases = [case for case in cases if case.id == args.case]
        if not cases:
            raise SystemExit(f"Unknown benchmark case: {args.case}")
    profiles = [item.strip() for item in args.profiles.split(",") if item.strip()]
    runner = BenchmarkRunner(args.output_dir)
    if args.quality_comparison:
        profile = profiles[0] if profiles else RunProfile.API_MOCK.value
        result = runner.run_quality_comparison(
            cases=cases,
            profile_name=profile,
            use_codex=args.codex,
            max_steps=args.max_steps,
        )
        print(json.dumps(asdict(result), ensure_ascii=False, indent=2))
        return 0 if result.summary["passed"] == result.summary["total"] else 1
    result = runner.run_suite(
        cases=cases,
        profiles=profiles,
        use_codex=args.codex,
        max_steps=args.max_steps,
    )
    print(json.dumps(asdict(result), ensure_ascii=False, indent=2))
    return 0 if result.summary["passed"] == result.summary["total"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
