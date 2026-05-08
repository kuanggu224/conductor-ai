"""Verify a Conductor run manifest without replaying executions."""

from __future__ import annotations

import argparse
import json

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
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    result = verify_manifest(args.manifest, check_files=not args.skip_file_checks)
    print(json.dumps(result.to_dict(), ensure_ascii=False, indent=2))
    return 0 if result.passed else 2


if __name__ == "__main__":
    raise SystemExit(main())
