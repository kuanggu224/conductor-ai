"""Diagnostics CLI tests."""

import json

from app.diagnostics import build_parser, main


def test_diagnostics_parser_accepts_project_root_and_probe() -> None:
    args = build_parser().parse_args(["--project-root", "demo", "--probe-cli", "--probe-llm", "--preflight-llm"])

    assert args.project_root == "demo"
    assert args.probe_cli is True
    assert args.probe_llm is True
    assert args.preflight_llm is True


def test_diagnostics_cli_prints_platform_snapshot(monkeypatch, tmp_path, capsys) -> None:
    monkeypatch.setattr(
        "conductor.diagnostics.discover_cli_tools",
        lambda: [type("Tool", (), {"name": "codex", "available": True})()],
    )

    exit_code = main(["--project-root", str(tmp_path)])

    payload = json.loads(capsys.readouterr().out)
    assert exit_code in {0, 2}
    assert payload["project_root"] == str(tmp_path.resolve())
    assert "config_paths" in payload
    assert "encoding" in payload
    assert "stdout_encoding" in payload["encoding"]
    assert "cli_tools" in payload
    assert payload["cli_tools"][0]["version_status"] == "not_checked"
    assert "available_cli_names" in payload
    assert "llm_backends" in payload
    assert payload["llm_backends"][0]["server_status"] == "not_checked"
