"""Platform diagnostics tests."""

from conductor.config.cli import CLISelectionConfig
from conductor.diagnostics import build_platform_diagnostics


def test_platform_diagnostics_marks_ready_bindings(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(
        "conductor.diagnostics.discover_cli_tools",
        lambda: [
            type("Tool", (), {"name": "codex", "available": True})(),
            type("Tool", (), {"name": "opencode", "available": False})(),
        ],
    )

    diagnostics = build_platform_diagnostics(
        cli_config=CLISelectionConfig(
            selected_cli_names=["codex"],
            role_cli_bindings={"designer": "codex"},
        ),
        project_root=tmp_path,
    )

    assert diagnostics.ok is True
    assert diagnostics.available_cli_names == ["codex"]
    assert diagnostics.role_bindings[0].status == "ready"
    assert diagnostics.to_dict()["project_root"] == str(tmp_path.resolve())


def test_platform_diagnostics_warns_for_missing_or_unselected_cli(monkeypatch) -> None:
    monkeypatch.setattr(
        "conductor.diagnostics.discover_cli_tools",
        lambda: [
            type("Tool", (), {"name": "codex", "available": True})(),
            type("Tool", (), {"name": "opencode", "available": False})(),
        ],
    )

    diagnostics = build_platform_diagnostics(
        cli_config=CLISelectionConfig(
            selected_cli_names=["opencode"],
            role_cli_bindings={
                "backend_engineer": "codex",
                "frontend_engineer": "opencode",
                "tester": None,
            },
        )
    )

    assert diagnostics.ok is False
    statuses = {item.role: item.status for item in diagnostics.role_bindings}
    assert statuses["backend_engineer"] == "not_selected"
    assert statuses["frontend_engineer"] == "missing"
    assert statuses["tester"] == "unbound"
    assert diagnostics.warnings
