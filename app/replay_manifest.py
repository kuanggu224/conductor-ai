"""Build a read-only replay trace from a Conductor run manifest."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

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
    parser.add_argument(
        "--output",
        help="Write the selected trace format to this file instead of printing the full trace.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    trace = build_manifest_replay_trace(args.manifest, check_files=not args.skip_file_checks)
    content = trace.to_markdown() if args.format == "markdown" else json.dumps(trace.to_dict(), ensure_ascii=False, indent=2)
    if args.output:
        output_path = Path(args.output).expanduser().resolve()
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(content, encoding="utf-8")
        print(
            json.dumps(
                {
                    "ok": trace.passed,
                    "output_path": str(output_path),
                    "format": args.format,
                    "project_id": trace.project_id,
                    "event_count": len(trace.events),
                    "verification": trace.verification.to_dict(),
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        return 0 if trace.passed else 2
    if args.format == "markdown":
        print(content, end="")
    else:
        print(content)
    return 0 if trace.passed else 2


if __name__ == "__main__":
    raise SystemExit(main())
