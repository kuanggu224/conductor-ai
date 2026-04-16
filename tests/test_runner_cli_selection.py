"""Runner 的基础测试命令选择逻辑测试。"""

from conductor.execution.runner import Runner
from conductor.state.store import InMemoryStateStore


def test_runner_prefers_uv_for_test_command(monkeypatch) -> None:
    monkeypatch.setattr(
        "conductor.execution.runner.shutil.which",
        lambda name: {"uv": "C:/Python/Scripts/uv.exe", "pytest": "C:/Python/Scripts/pytest.exe"}.get(name),
    )
    runner = Runner(state_store=InMemoryStateStore(), enable_tester_harness=True)

    assert runner._select_test_command() == ["uv", "run", "python", "-m", "pytest", "-q"]


def test_runner_falls_back_to_pytest_then_python(monkeypatch) -> None:
    monkeypatch.setattr(
        "conductor.execution.runner.shutil.which",
        lambda name: {"pytest": "C:/Python/Scripts/pytest.exe"}.get(name),
    )
    pytest_runner = Runner(state_store=InMemoryStateStore(), enable_tester_harness=True)

    assert pytest_runner._select_test_command() == ["pytest", "-q"]

    monkeypatch.setattr("conductor.execution.runner.shutil.which", lambda name: None)
    python_runner = Runner(state_store=InMemoryStateStore(), enable_tester_harness=True)
    assert python_runner._select_test_command()[1:] == ["-m", "pytest", "-q"]
