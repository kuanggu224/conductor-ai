"""Compatibility entrypoint for the standalone to-do app."""

from __future__ import annotations

import sys

from app.todo_app import app as todo_app
from app.todo_app import create_app
from app.todo_app import main as todo_main

app = todo_app

__all__ = ["app", "create_app", "main", "parse_requirement"]


def parse_requirement(argv: list[str]) -> str:
    """Legacy helper kept for compatibility with existing tests."""
    if len(argv) < 2:
        raise ValueError('Please pass a requirement, for example: python app/main.py "Implement a simple to-do app"')
    return " ".join(argv[1:]).strip()


def main(argv: list[str] | None = None) -> int | None:
    """Delegate to the to-do application entrypoint."""
    args = list(argv) if argv is not None else None
    if args is not None and args and not args[0].startswith("-"):
        # Preserve the legacy script-style invocation used by older callers
        # while still allowing any real CLI flags that follow the requirement text.
        first_flag_index = next((index for index, token in enumerate(args) if token.startswith("-")), len(args))
        args = args[first_flag_index:] if first_flag_index < len(args) else []
    result = todo_main(args)
    return 0 if result is None else result


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
