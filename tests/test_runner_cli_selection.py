"""Runner 的基础测试命令选择逻辑测试。"""

from conductor.execution.runner import Runner
from conductor.state.store import InMemoryStateStore


def test_runner_uses_current_python_for_pytest_project(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(
        "conductor.execution.runner.shutil.which",
        lambda name: {"uv": "C:/Python/Scripts/uv.exe", "pytest": "C:/Python/Scripts/pytest.exe"}.get(name),
    )
    (tmp_path / "tests").mkdir()
    runner = Runner(state_store=InMemoryStateStore(), enable_tester_harness=True)

    command = runner._select_test_command(str(tmp_path))
    assert command[1:] == ["-m", "pytest", "-q"]


def test_runner_uses_npm_test_for_node_project(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(
        "conductor.execution.runner.shutil.which",
        lambda name: {"npm": "C:/Program Files/nodejs/npm.cmd"}.get(name),
    )
    (tmp_path / "package.json").write_text('{"scripts":{"test":"node --test"}}', encoding="utf-8")
    runner = Runner(state_store=InMemoryStateStore(), enable_tester_harness=True)

    assert runner._select_test_command(str(tmp_path)) == ["npm", "test"]


def test_runner_prefers_pytest_for_fullstack_project_with_static_assets(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr("conductor.execution.runner.shutil.which", lambda name: None)
    (tmp_path / "tests").mkdir()
    (tmp_path / "pytest.ini").write_text("[pytest]\n", encoding="utf-8")
    (tmp_path / "index.html").write_text("<main></main>", encoding="utf-8")
    (tmp_path / "static").mkdir()
    (tmp_path / "static" / "app.js").write_text("fetch('/api/items')", encoding="utf-8")
    runner = Runner(state_store=InMemoryStateStore(), enable_tester_harness=True)

    command = runner._select_test_command(str(tmp_path))

    assert command[1:] == ["-m", "pytest", "-q"]


def test_runner_falls_back_to_pytest_then_python(monkeypatch) -> None:
    monkeypatch.setattr(
        "conductor.execution.runner.shutil.which",
        lambda name: {"pytest": "C:/Python/Scripts/pytest.exe"}.get(name),
    )
    pytest_runner = Runner(state_store=InMemoryStateStore(), enable_tester_harness=True)

    assert pytest_runner._select_test_command()[1:] == ["-m", "pytest", "-q"]

    monkeypatch.setattr("conductor.execution.runner.shutil.which", lambda name: None)
    python_runner = Runner(state_store=InMemoryStateStore(), enable_tester_harness=True)
    assert python_runner._select_test_command()[1:] == ["-m", "pytest", "-q"]
