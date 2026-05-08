"""Build a read-only replay trace from a Conductor run manifest."""

from __future__ import annotations

import argparse
import json

from conductor.io.encoding import configure_utf8_stdio
from conductor.replay_trace import build_manifest_replay_trace

configure_utf8_stdio()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m app.replay_manifest")
    parser.add_argument("manifest", help="Path to a Conductor *.manifest.json file.")
    parser.add_argument(
        "--format",
        choices=["json", "markdown"],
        default="json",
        help="Output format.",
    )
    parser.add_argument(
        "--skip-file-checks",
        action="store_true",
        help="Skip referenced file existence checks while building the trace.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    trace = build_manifest_replay_trace(args.manifest, check_files=not args.skip_file_checks)
    if args.format == "markdown":
        print(trace.to_markdown(), end="")
    else:
        print(json.dumps(trace.to_dict(), ensure_ascii=False, indent=2))
    return 0 if trace.passed else 2


if __name__ == "__main__":
    raise SystemExit(main())
