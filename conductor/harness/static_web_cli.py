"""CLI entry point for static web validation."""

from __future__ import annotations

import argparse
import sys

from conductor.harness.models import HarnessRequest
from conductor.harness.static_web import StaticWebHarness


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Validate a static web project")
    parser.add_argument("--root", default=".", help="Project root containing index.html")
    args = parser.parse_args(argv)

    result = StaticWebHarness().run(HarnessRequest(command=[], working_directory=args.root))
    sys.stdout.write(result.stdout)
    if result.stderr:
        sys.stderr.write(result.stderr)
    return result.exit_code


if __name__ == "__main__":
    raise SystemExit(main())
