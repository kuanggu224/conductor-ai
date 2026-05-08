"""Verify a Conductor audit bundle index."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from conductor.audit_bundle import verify_audit_bundle
from conductor.io.encoding import configure_utf8_stdio

configure_utf8_stdio()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m app.verify_audit_bundle")
    parser.add_argument("bundle", help="Path to a Conductor *.audit.json bundle index.")
    parser.add_argument(
        "--skip-file-checks",
        action="store_true",
        help="Only verify bundle self-consistency; do not check referenced files exist.",
    )
    parser.add_argument(
        "--fail-on-warnings",
        action="store_true",
        help="Exit with code 2 when warnings are present. Useful for CI or production gates.",
    )
    parser.add_argument("--output", help="Write the JSON verification report to this file.")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    result = verify_audit_bundle(args.bundle, check_files=not args.skip_file_checks)
    rendered = json.dumps(result.to_dict(), ensure_ascii=False, indent=2)
    ok = result.passed and (not args.fail_on_warnings or not result.warnings)
    if args.output:
        output_path = Path(args.output).expanduser().resolve()
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(rendered, encoding="utf-8")
        print(
            json.dumps(
                {
                    "ok": ok,
                    "output_path": str(output_path),
                    "project_id": result.project_id,
                    "error_count": len(result.errors),
                    "warning_count": len(result.warnings),
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        return 0 if ok else 2
    print(rendered)
    return 0 if ok else 2


if __name__ == "__main__":
    raise SystemExit(main())
