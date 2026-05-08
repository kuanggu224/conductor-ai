"""Verify a Conductor run manifest without replaying executions."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from conductor.io.encoding import configure_utf8_stdio
from conductor.replay_verifier import verify_manifest

configure_utf8_stdio()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m app.verify_manifest")
    parser.add_argument("manifest", help="Path to a Conductor *.manifest.json file.")
    parser.add_argument(
        "--skip-file-checks",
        action="store_true",
        help="Only verify manifest self-consistency; do not check referenced files exist.",
    )
    parser.add_argument("--output", help="Write the JSON verification report to this file.")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    result = verify_manifest(args.manifest, check_files=not args.skip_file_checks)
    payload = result.to_dict()
    rendered = json.dumps(payload, ensure_ascii=False, indent=2)
    if args.output:
        output_path = Path(args.output).expanduser().resolve()
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(rendered, encoding="utf-8")
        print(
            json.dumps(
                {
                    "ok": result.passed,
                    "output_path": str(output_path),
                    "project_id": result.project_id,
                    "error_count": len(result.errors),
                    "warning_count": len(result.warnings),
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        return 0 if result.passed else 2
    print(rendered)
    return 0 if result.passed else 2


if __name__ == "__main__":
    raise SystemExit(main())
