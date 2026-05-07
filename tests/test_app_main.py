"""Entrypoint parameter tests."""

from app import main as app_main
from app.main import parse_requirement


def test_parse_requirement_supports_natural_language_cli_input() -> None:
    requirement = parse_requirement(["app/main.py", "implement", "a", "simple", "todo", "app"])

    assert requirement == "implement a simple todo app"


def test_main_ignores_legacy_requirement_text() -> None:
    captured: list[list[str] | None] = []

    def fake_todo_main(argv: list[str] | None = None) -> None:
        captured.append(argv)

    original = app_main.todo_main
    try:
        app_main.todo_main = fake_todo_main  # type: ignore[assignment]
        app_main.main(["Implement a simple to-do app"])
    finally:
        app_main.todo_main = original  # type: ignore[assignment]

    assert captured == [[]]


def test_main_keeps_cli_flags_after_legacy_requirement_text() -> None:
    captured: list[list[str] | None] = []

    def fake_todo_main(argv: list[str] | None = None) -> None:
        captured.append(argv)

    original = app_main.todo_main
    try:
        app_main.todo_main = fake_todo_main  # type: ignore[assignment]
        app_main.main(["Implement", "a", "simple", "to-do", "app", "--host", "0.0.0.0"])
    finally:
        app_main.todo_main = original  # type: ignore[assignment]

    assert captured == [["--host", "0.0.0.0"]]


def test_main_returns_the_underlying_entrypoint_exit_code() -> None:
    def fake_todo_main(argv: list[str] | None = None) -> int:
        return 7

    original = app_main.todo_main
    try:
        app_main.todo_main = fake_todo_main  # type: ignore[assignment]
        result = app_main.main(["--host", "0.0.0.0"])
    finally:
        app_main.todo_main = original  # type: ignore[assignment]

    assert result == 7
