"""Print Conductor platform diagnostics."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from conductor.config.llm import load_llm_runtime_config
from conductor.diagnostics import build_platform_diagnostics, build_requirement_llm_preflight_probe
from conductor.io.encoding import configure_utf8_stdio

configure_utf8_stdio()


def build_parser() -> argparse.ArgumentParser:
    """Build the diagnostics CLI parser."""
    parser = argparse.ArgumentParser(prog="python -m app.diagnostics")
    parser.add_argument("--project-root", default=str(Path.cwd()), help="Project root used for path diagnostics.")
    parser.add_argument(
        "--probe-llm",
        action="store_true",
        help="Probe enabled OpenAI-compatible LLM /models endpoints.",
    )
    parser.add_argument(
        "--preflight-llm",
        action="store_true",
        help="Run a lightweight chat-completion preflight for enabled LLM backends.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    """Run diagnostics and print a JSON payload."""
    args = build_parser().parse_args(argv)
    llm_runtime_config = load_llm_runtime_config()
    diagnostics = build_platform_diagnostics(
        project_root=args.project_root,
        llm_runtime_config=llm_runtime_config,
        probe_llm=args.probe_llm or args.preflight_llm,
        preflight_probe=(
            build_requirement_llm_preflight_probe(
                llm_runtime_config,
                Path(args.project_root).expanduser().resolve() / ".conductor" / "diagnostics",
            )
            if args.preflight_llm
            else None
        ),
    )
    print(json.dumps(diagnostics.to_dict(), ensure_ascii=False, indent=2))
    return 0 if diagnostics.ok else 2

if __name__ == "__main__":
    raise SystemExit(main())
