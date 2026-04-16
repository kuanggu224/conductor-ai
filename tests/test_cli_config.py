"""CLI 配置与扫描测试。"""

from conductor.config.cli import (
    CLISelectionConfig,
    build_cli_options,
    build_role_cli_binding_options,
    discover_cli_tools,
    load_cli_selection_config,
    save_cli_selection_config,
)


def test_discover_cli_tools_marks_available(monkeypatch) -> None:
    fake_paths = {
        "codex": "C:/Users/user/AppData/Roaming/npm/codex.ps1",
        "claude": "C:/Users/user/AppData/Roaming/npm/claude.ps1",
        "qwen": None,
    }

    monkeypatch.setattr(
        "conductor.config.cli.shutil.which",
        lambda name: fake_paths.get(name),
    )

    tools = discover_cli_tools()
    python_tool = next(tool for tool in tools if tool.name == "codex")
    uv_tool = next(tool for tool in tools if tool.name == "claude")
    pytest_tool = next(tool for tool in tools if tool.name == "qwen")

    assert python_tool.available is True
    assert uv_tool.available is True
    assert pytest_tool.available is False


def test_cli_selection_config_round_trip(tmp_path) -> None:
    path = tmp_path / "cli.config.json"
    config = CLISelectionConfig(
        selected_cli_names=["codex", "claude"],
        role_cli_bindings={"tester": "qwen", "backend_engineer": "codex"},
    )

    save_cli_selection_config(config, path)
    loaded = load_cli_selection_config(path)

    assert loaded.selected_cli_names == ["codex", "claude"]
    assert loaded.role_cli_bindings["tester"] == "qwen"
    assert loaded.role_cli_bindings["backend_engineer"] == "codex"


def test_build_cli_options_marks_selected(monkeypatch) -> None:
    monkeypatch.setattr(
        "conductor.config.cli.discover_cli_tools",
        lambda: [
            type("Tool", (), {"name": "codex", "label": "Codex CLI", "path": "C:/npm/codex.ps1", "available": True})(),
            type("Tool", (), {"name": "qwen", "label": "Qwen CLI", "path": None, "available": False})(),
        ],
    )

    options = build_cli_options(CLISelectionConfig(selected_cli_names=["codex"]))

    assert options[0]["selected"] is True
    assert options[0]["available"] is True
    assert options[1]["selected"] is False
    assert options[1]["available"] is False


def test_build_role_cli_binding_options_filters_to_selected_available_tools(monkeypatch) -> None:
    monkeypatch.setattr(
        "conductor.config.cli.discover_cli_tools",
        lambda: [
            type("Tool", (), {"name": "codex", "label": "Codex CLI", "path": "C:/npm/codex.ps1", "available": True})(),
            type("Tool", (), {"name": "claude", "label": "Claude Code CLI", "path": "C:/npm/claude.ps1", "available": True})(),
            type("Tool", (), {"name": "qwen", "label": "Qwen CLI", "path": "C:/npm/qwen.ps1", "available": True})(),
        ],
    )

    options = build_role_cli_binding_options(
        CLISelectionConfig(
            selected_cli_names=["codex", "claude"],
            role_cli_bindings={"tester": "codex"},
        )
    )

    tester_option = next(option for option in options if option["role_name"] == "tester")
    choice_names = [choice["name"] for choice in tester_option["choices"]]
    assert "" in choice_names
    assert "codex" in choice_names
    assert "claude" in choice_names
    assert "qwen" not in choice_names
    assert tester_option["selected_cli"] == "codex"
