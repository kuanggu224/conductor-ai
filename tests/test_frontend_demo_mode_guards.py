"""Regression checks for frontend demo-mode safety guards."""

from __future__ import annotations

from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
MAIN_JS = REPO_ROOT / "frontend" / "src" / "main.js"


def _source() -> str:
    return MAIN_JS.read_text(encoding="utf-8")


def _function_body(source: str, name: str) -> str:
    marker = f"function {name}"
    start = source.find(marker)
    if start < 0:
        marker = f"async function {name}"
        start = source.find(marker)
    assert start >= 0, f"{name} not found"

    brace_start = source.find("{", start)
    assert brace_start >= 0, f"{name} has no body"
    depth = 0
    for index in range(brace_start, len(source)):
        char = source[index]
        if char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                return source[brace_start : index + 1]
    raise AssertionError(f"{name} body is not closed")


def _first_api_call(body: str) -> int:
    indexes = [index for index in (body.find("api."), body.find("api[")) if index >= 0]
    return min(indexes) if indexes else -1


def test_demo_mode_short_circuits_global_backend_entry_points() -> None:
    source = _source()

    refresh_all = _function_body(source, "refreshAll")
    load_project = _function_body(source, "loadProject")
    render_settings = _function_body(source, "renderSettings")
    wire_runtime_stream = _function_body(source, "wireRuntimeStream")

    assert "if (state.demoMode)" in refresh_all
    assert "if (state.demoMode)" in load_project
    assert "if (state.demoMode) return renderDemoSettings();" in render_settings
    assert "state.demoMode || !state.selectedProjectId" in wire_runtime_stream


def test_demo_mode_guards_user_actions_before_api_calls() -> None:
    source = _source()
    guarded_actions = [
        "loadTaskContext",
        "loadTaskAgents",
        "claimTask",
        "claimNextTask",
        "claimBatch",
        "completeTask",
        "failTask",
        "heartbeatTask",
        "releaseTask",
        "releaseStale",
        "releaseExpired",
        "sweepTasks",
        "claimAgentTask",
        "listAgentTasks",
        "loadArtifact",
        "loadHumanControl",
        "loadLiveState",
        "humanAction",
        "loadDiagnostics",
        "loadTodos",
        "createTodo",
        "loadTodo",
        "updateTodo",
        "deleteTodo",
    ]

    for name in guarded_actions:
        body = _function_body(source, name)
        first_api = _first_api_call(body)
        first_guard = body.find("demoAction(")
        assert first_api >= 0, f"{name} should contain a live API path"
        assert first_guard >= 0, f"{name} is missing demoAction guard"
        assert first_guard < first_api, f"{name} calls api before demoAction"


def test_demo_mode_guards_command_level_api_calls() -> None:
    source = _source()
    command_guards = {
        "openCreateProject": "if (state.demoMode)",
        "projectCommand": "if (state.demoMode)",
        "runOperation": "if (state.demoMode)",
    }

    for name, guard in command_guards.items():
        body = _function_body(source, name)
        first_api = _first_api_call(body)
        first_guard = body.find(guard)
        assert first_api >= 0, f"{name} should contain a live API path"
        assert first_guard >= 0, f"{name} is missing {guard}"
        assert first_guard < first_api, f"{name} calls api before {guard}"


def test_demo_todos_use_mutable_local_state() -> None:
    source = _source()
    render_todos = _function_body(source, "renderTodos")
    create_todo = _function_body(source, "createTodo")
    update_todo = _function_body(source, "updateTodo")
    delete_todo = _function_body(source, "deleteTodo")

    assert "ensureDemoTodos()" in render_todos
    assert "demoTodos()" not in render_todos
    assert "createDemoTodo(payload)" in create_todo
    assert "updateDemoTodo(id, payload)" in update_todo
    assert "deleteDemoTodo(id)" in delete_todo
    assert "function demoTodoStatsFromItems" in source
