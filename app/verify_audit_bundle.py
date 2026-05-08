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
    parser.add_argument("bundle", help="Path to a Conductor *.audit.json bundle index, or a directory containing bundles.")
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
    target = Path(args.bundle).expanduser()
    if target.is_dir():
        payload = _verify_bundle_directory(
            target,
            check_files=not args.skip_file_checks,
            fail_on_warnings=args.fail_on_warnings,
        )
        rendered = json.dumps(payload, ensure_ascii=False, indent=2)
        ok = bool(payload["passed"])
    else:
        result = verify_audit_bundle(target, check_files=not args.skip_file_checks)
        payload = result.to_dict()
        rendered = json.dumps(payload, ensure_ascii=False, indent=2)
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
                    "project_id": payload.get("project_id", ""),
                    "bundle_count": payload.get("bundle_count", 1),
                    "error_count": payload.get("error_count", 0),
                    "warning_count": payload.get("warning_count", 0),
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        return 0 if ok else 2
    print(rendered)
    return 0 if ok else 2


def _verify_bundle_directory(
    directory: Path,
    *,
    check_files: bool,
    fail_on_warnings: bool,
) -> dict[str, object]:
    bundle_paths = sorted(directory.rglob("*.audit.json"))
    results = [
        verify_audit_bundle(path, check_files=check_files).to_dict()
        for path in bundle_paths
    ]
    error_count = sum(int(result.get("error_count", 0)) for result in results)
    warning_count = sum(int(result.get("warning_count", 0)) for result in results)
    errors = [] if bundle_paths else [f"no audit bundles found under: {directory}"]
    if not bundle_paths:
        error_count = 1
    passed = error_count == 0 and (not fail_on_warnings or warning_count == 0)
    return {
        "bundle_dir": str(directory.resolve()),
        "bundle_count": len(bundle_paths),
        "passed": passed,
        "error_count": error_count,
        "warning_count": warning_count,
        "errors": errors,
        "results": results,
    }


if __name__ == "__main__":
    raise SystemExit(main())
