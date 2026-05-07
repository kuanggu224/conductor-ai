"""Platform diagnostics tests."""

from conductor.config.cli import CLISelectionConfig
from conductor.agents.llm import LLMHTTPConfig
from conductor.config.llm import LLMRuntimeConfig, LLMUsagePolicy
from conductor.diagnostics import build_platform_diagnostics, build_requirement_llm_preflight_probe


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
    assert diagnostics.cli_tools[0].name == "codex"
    assert diagnostics.cli_tools[0].status == "available"
    assert diagnostics.cli_tools[0].version_status == "not_checked"
    assert diagnostics.role_bindings[0].status == "ready"
    assert diagnostics.to_dict()["project_root"] == str(tmp_path.resolve())
    assert diagnostics.encoding.preferred_encoding
    assert "encoding" in diagnostics.to_dict()


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


def test_platform_diagnostics_can_probe_cli_versions(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(
        "conductor.diagnostics.discover_cli_tools",
        lambda: [
            type("Tool", (), {"name": "codex", "label": "Codex CLI", "path": "/bin/codex", "available": True})(),
            type("Tool", (), {"name": "opencode", "label": "OpenCode CLI", "path": "", "available": False})(),
        ],
    )
    calls = []

    def fake_cli_probe(path, timeout_seconds):
        calls.append((path, timeout_seconds))
        return "ok", "codex 1.2.3", ""

    diagnostics = build_platform_diagnostics(
        cli_config=CLISelectionConfig(
            selected_cli_names=["codex"],
            role_cli_bindings={"designer": "codex"},
        ),
        project_root=tmp_path,
        probe_cli=True,
        cli_probe=fake_cli_probe,
    )

    codex = {item.name: item for item in diagnostics.cli_tools}["codex"]
    opencode = {item.name: item for item in diagnostics.cli_tools}["opencode"]
    assert diagnostics.ok is True
    assert calls == [("/bin/codex", 5.0)]
    assert codex.selected is True
    assert codex.version_status == "ok"
    assert codex.version_output == "codex 1.2.3"
    assert opencode.version_status == "not_available"


def test_platform_diagnostics_warns_when_selected_cli_probe_fails(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(
        "conductor.diagnostics.discover_cli_tools",
        lambda: [
            type("Tool", (), {"name": "codex", "label": "Codex CLI", "path": "/bin/codex", "available": True})(),
        ],
    )

    diagnostics = build_platform_diagnostics(
        cli_config=CLISelectionConfig(
            selected_cli_names=["codex"],
            role_cli_bindings={"designer": "codex"},
        ),
        project_root=tmp_path,
        probe_cli=True,
        cli_probe=lambda *_: ("failed", "", "exit_code=1"),
    )

    assert diagnostics.ok is False
    assert any("Selected CLI `codex` probe failed" in warning for warning in diagnostics.warnings)


def test_platform_diagnostics_reports_llm_backend_config_without_probe(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr("conductor.diagnostics.discover_cli_tools", lambda: [])

    diagnostics = build_platform_diagnostics(
        cli_config=CLISelectionConfig(),
        project_root=tmp_path,
        llm_runtime_config=LLMRuntimeConfig(
            local=LLMHTTPConfig(
                base_url="http://127.0.0.1:1234/v1",
                model_name="local-model",
                enabled=False,
            ),
            cloud=LLMHTTPConfig(
                base_url="https://example.com/v1",
                model_name="cloud-model",
                api_key="secret",
                timeout_seconds=42,
                enabled=True,
            ),
            usage=LLMUsagePolicy(),
        ),
    )

    cloud = {item.backend: item for item in diagnostics.llm_backends}["cloud"]
    assert cloud.enabled is True
    assert cloud.model == "cloud-model"
    assert cloud.timeout_seconds == 42
    assert cloud.api_key_present is True
    assert cloud.server_status == "not_checked"
    assert "preferred=" in cloud.encoding
    assert "stdout=" in cloud.encoding


def test_platform_diagnostics_can_probe_llm_models_and_preflight(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr("conductor.diagnostics.discover_cli_tools", lambda: [])

    def fake_model_probe(base_url, api_key, timeout):
        assert base_url == "https://example.com/v1"
        assert api_key == "secret"
        assert timeout == 10
        return "reachable", ["cloud-model", "backup-model"], 32768, ""

    def fake_preflight(backend):
        assert backend == "cloud"
        return True, ""

    diagnostics = build_platform_diagnostics(
        cli_config=CLISelectionConfig(),
        project_root=tmp_path,
        llm_runtime_config=LLMRuntimeConfig(
            local=LLMHTTPConfig(
                base_url="http://127.0.0.1:1234/v1",
                model_name="local-model",
                enabled=False,
            ),
            cloud=LLMHTTPConfig(
                base_url="https://example.com/v1",
                model_name="cloud-model",
                api_key="secret",
                timeout_seconds=10,
                enabled=True,
            ),
            usage=LLMUsagePolicy(),
        ),
        probe_llm=True,
        model_probe=fake_model_probe,
        preflight_probe=fake_preflight,
    )

    cloud = {item.backend: item for item in diagnostics.llm_backends}["cloud"]
    assert diagnostics.ok is True
    assert cloud.server_status == "reachable"
    assert cloud.available_models == ["cloud-model", "backup-model"]
    assert cloud.context_length == 32768
    assert cloud.preflight_success is True


def test_platform_diagnostics_does_not_fail_when_models_endpoint_is_missing_but_preflight_passes(
    monkeypatch,
    tmp_path,
) -> None:
    monkeypatch.setattr("conductor.diagnostics.discover_cli_tools", lambda: [])

    diagnostics = build_platform_diagnostics(
        cli_config=CLISelectionConfig(),
        project_root=tmp_path,
        llm_runtime_config=LLMRuntimeConfig(
            local=LLMHTTPConfig(
                base_url="http://127.0.0.1:1234/v1",
                model_name="local-model",
                enabled=False,
            ),
            cloud=LLMHTTPConfig(
                base_url="https://example.com/v1",
                model_name="cloud-model",
                api_key="secret",
                enabled=True,
            ),
            usage=LLMUsagePolicy(),
        ),
        probe_llm=True,
        model_probe=lambda *_: ("models_unavailable", [], None, "HTTP 404"),
        preflight_probe=lambda backend: (True, ""),
    )

    cloud = {item.backend: item for item in diagnostics.llm_backends}["cloud"]
    assert diagnostics.ok is True
    assert cloud.server_status == "models_unavailable"
    assert cloud.model_list_error == "HTTP 404"
    assert cloud.preflight_success is True
    assert cloud.preflight_error == ""


def test_requirement_llm_preflight_probe_selects_backend_config(monkeypatch, tmp_path) -> None:
    calls = []

    def fake_preflight(*, backend, config, output_dir):
        calls.append((backend, config.model_name, output_dir))
        return type("Result", (), {"success": True, "error": ""})()

    monkeypatch.setattr("conductor.requirement_benchmark.run_requirement_llm_preflight", fake_preflight)
    runtime_config = LLMRuntimeConfig(
        local=LLMHTTPConfig(base_url="http://local.test/v1", model_name="local-model"),
        cloud=LLMHTTPConfig(base_url="https://cloud.test/v1", model_name="cloud-model", enabled=True),
        usage=LLMUsagePolicy(),
    )

    probe = build_requirement_llm_preflight_probe(runtime_config, tmp_path)

    assert probe("cloud") == (True, "")
    assert calls == [("cloud", "cloud-model", tmp_path)]
