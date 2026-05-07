from fastapi import FastAPI

from conductor.todo import create_todo_service
from app.todo_app import create_app, create_todo_service, main
from app import todo_app as todo_app_module
from conductor.todo import TodoService


def test_todo_app_factory_builds_a_fastapi_application() -> None:
    app = create_app()

    assert isinstance(app, FastAPI)
    assert app.title == "Simple Todo App"


def test_todo_app_exports_a_todo_service_factory() -> None:
    service = create_todo_service()

    assert isinstance(service, TodoService)


def test_todo_app_main_help_exits_cleanly() -> None:
    try:
        main(["--help"])
    except SystemExit as error:
        assert error.code == 0
    else:
        raise AssertionError("expected SystemExit")


def test_todo_app_main_ignores_legacy_requirement_text() -> None:
    captured: list[list[str] | None] = []

    def fake_run(*args, **kwargs) -> None:  # type: ignore[no-untyped-def]
        captured.append([args, kwargs])

    original = main.__globals__["uvicorn"].run
    try:
        main.__globals__["uvicorn"].run = fake_run  # type: ignore[assignment]
        main(["Implement a simple to-do app"])
    finally:
        main.__globals__["uvicorn"].run = original  # type: ignore[assignment]

    assert captured and captured[0][1]["host"] == "127.0.0.1"


def test_todo_app_main_keeps_cli_flags_after_legacy_requirement_text() -> None:
    captured: list[dict[str, object]] = []

    def fake_run(*args, **kwargs) -> None:  # type: ignore[no-untyped-def]
        captured.append(kwargs)

    original = main.__globals__["uvicorn"].run
    try:
        main.__globals__["uvicorn"].run = fake_run  # type: ignore[assignment]
        main(["Implement a simple to-do app", "--host", "0.0.0.0", "--port", "9001"])
    finally:
        main.__globals__["uvicorn"].run = original  # type: ignore[assignment]

    assert captured
    assert captured[0]["host"] == "0.0.0.0"
    assert captured[0]["port"] == 9001


def test_todo_app_main_uses_sys_argv_when_no_arguments_are_passed() -> None:
    captured: list[list[str] | None] = []

    def fake_run(*args, **kwargs) -> None:  # type: ignore[no-untyped-def]
        captured.append(kwargs)

    original_run = todo_app_module.uvicorn.run
    original_argv = todo_app_module.sys.argv
    try:
        todo_app_module.uvicorn.run = fake_run  # type: ignore[assignment]
        todo_app_module.sys.argv = ["app/todo_app.py", "Implement a simple to-do app", "--port", "9001"]
        todo_app_module.main()
    finally:
        todo_app_module.uvicorn.run = original_run  # type: ignore[assignment]
        todo_app_module.sys.argv = original_argv  # type: ignore[assignment]

    assert captured
    assert captured[0]["port"] == 9001


def test_todo_package_exports_service_factory() -> None:
    service = create_todo_service()

    assert service.list_todos() == []
