"""Platform diagnostics tests."""

from conductor.preflight_gate import write_preflight_gate_payload
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
    assert isinstance(diagnostics.encoding.utf8_ready, bool)
    assert isinstance(diagnostics.encoding.warnings, list)
    assert diagnostics.encoding.recommendation
    assert diagnostics.to_dict()["preflight_gate"]["recorded"] is False


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
    assert codex.auth_status == "unknown"
    assert "auth/status" in codex.recommendation
    assert opencode.version_status == "not_available"
    assert opencode.auth_status == "not_available"


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


def test_platform_diagnostics_reports_selected_cli_auth_failure(monkeypatch, tmp_path) -> None:
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
        cli_probe=lambda *_: ("failed", "", "authentication required: please log in"),
    )

    codex = diagnostics.cli_tools[0]
    assert diagnostics.ok is False
    assert codex.auth_status == "unauthorized"
    assert "authentication required" in codex.auth_error
    assert "login/auth" in codex.recommendation
    assert any("appears unauthorized" in warning for warning in diagnostics.warnings)


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
    assert cloud.health_status == "configured"
    assert "preflight" in cloud.recommendation
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
    assert cloud.selected_model_available is True
    assert cloud.context_length == 32768
    assert cloud.preflight_success is True
    assert cloud.health_status == "ready"
    assert "ready" in cloud.recommendation


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
    assert cloud.health_status == "ready"


def test_platform_diagnostics_classifies_llm_quota_preflight_failure(monkeypatch, tmp_path) -> None:
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
                timeout_seconds=30,
                enabled=True,
            ),
            usage=LLMUsagePolicy(),
        ),
        probe_llm=True,
        model_probe=lambda *_: ("reachable", ["cloud-model"], 32768, ""),
        preflight_probe=lambda backend: (False, "HTTP 429 insufficient_quota: billing limit reached"),
    )

    cloud = {item.backend: item for item in diagnostics.llm_backends}["cloud"]
    assert diagnostics.ok is False
    assert cloud.health_status == "failed"
    assert cloud.failure_category == "quota"
    assert "quota, billing, rate limits" in cloud.recommendation
    assert any("cloud LLM preflight failed (quota)" in warning for warning in diagnostics.warnings)


def test_platform_diagnostics_classifies_llm_context_length_failure(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr("conductor.diagnostics.discover_cli_tools", lambda: [])

    diagnostics = build_platform_diagnostics(
        cli_config=CLISelectionConfig(),
        project_root=tmp_path,
        llm_runtime_config=LLMRuntimeConfig(
            local=LLMHTTPConfig(
                base_url="http://127.0.0.1:1234/v1",
                model_name="local-model",
                timeout_seconds=30,
                enabled=True,
            ),
            cloud=LLMHTTPConfig(
                base_url="https://example.com/v1",
                model_name="cloud-model",
                enabled=False,
            ),
            usage=LLMUsagePolicy(),
        ),
        probe_llm=True,
        model_probe=lambda *_: ("reachable", ["local-model"], 4096, ""),
        preflight_probe=lambda backend: (False, "maximum context length exceeded: too many tokens"),
    )

    local = {item.backend: item for item in diagnostics.llm_backends}["local"]
    assert diagnostics.ok is False
    assert local.failure_category == "context_length"
    assert "larger-context model" in local.recommendation


def test_platform_diagnostics_warns_when_configured_llm_model_is_not_listed(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr("conductor.diagnostics.discover_cli_tools", lambda: [])

    diagnostics = build_platform_diagnostics(
        cli_config=CLISelectionConfig(),
        project_root=tmp_path,
        llm_runtime_config=LLMRuntimeConfig(
            local=LLMHTTPConfig(
                base_url="http://127.0.0.1:1234/v1",
                model_name="missing-model",
                timeout_seconds=10,
                enabled=True,
            ),
            cloud=LLMHTTPConfig(
                base_url="https://example.com/v1",
                model_name="cloud-model",
                enabled=False,
            ),
            usage=LLMUsagePolicy(),
        ),
        probe_llm=True,
        model_probe=lambda *_: ("reachable", ["other-model"], 4096, ""),
    )

    local = {item.backend: item for item in diagnostics.llm_backends}["local"]
    assert diagnostics.ok is False
    assert local.selected_model_available is False
    assert local.health_status == "warning"
    assert "Choose one of the listed models" in local.recommendation
    assert any("missing-model" in warning for warning in diagnostics.warnings)


def test_platform_diagnostics_reports_low_llm_timeout_without_failing(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr("conductor.diagnostics.discover_cli_tools", lambda: [])

    diagnostics = build_platform_diagnostics(
        cli_config=CLISelectionConfig(),
        project_root=tmp_path,
        llm_runtime_config=LLMRuntimeConfig(
            local=LLMHTTPConfig(
                base_url="http://127.0.0.1:1234/v1",
                model_name="local-model",
                timeout_seconds=5,
                enabled=True,
            ),
            cloud=LLMHTTPConfig(
                base_url="https://example.com/v1",
                model_name="cloud-model",
                enabled=False,
            ),
            usage=LLMUsagePolicy(),
        ),
    )

    local = {item.backend: item for item in diagnostics.llm_backends}["local"]
    assert diagnostics.ok is True
    assert local.timeout_status == "low"
    assert "below 10s" in local.timeout_warning
    assert "timeout is low" in local.recommendation


def test_platform_diagnostics_blocks_probe_when_llm_timeout_is_invalid(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr("conductor.diagnostics.discover_cli_tools", lambda: [])
    calls = []

    def fake_model_probe(*args):
        calls.append(args)
        return "reachable", ["local-model"], 32768, ""

    diagnostics = build_platform_diagnostics(
        cli_config=CLISelectionConfig(),
        project_root=tmp_path,
        llm_runtime_config=LLMRuntimeConfig(
            local=LLMHTTPConfig(
                base_url="http://127.0.0.1:1234/v1",
                model_name="local-model",
                timeout_seconds=0,
                enabled=True,
            ),
            cloud=LLMHTTPConfig(
                base_url="https://example.com/v1",
                model_name="cloud-model",
                enabled=False,
            ),
            usage=LLMUsagePolicy(),
        ),
        probe_llm=True,
        model_probe=fake_model_probe,
    )

    local = {item.backend: item for item in diagnostics.llm_backends}["local"]
    assert diagnostics.ok is False
    assert calls == []
    assert local.timeout_status == "invalid"
    assert local.server_status == "invalid_timeout"
    assert local.health_status == "failed"
    assert local.model_list_error == "timeout_seconds must be greater than 0"
    assert any("timeout is invalid" in warning for warning in diagnostics.warnings)


def test_platform_diagnostics_includes_persisted_preflight_gate_snapshot(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr("conductor.diagnostics.discover_cli_tools", lambda: [])
    write_preflight_gate_payload(
        tmp_path,
        {
            "ok": False,
            "preflight_gate": {
                "errors": ["local LLM preflight failed"],
                "recommendations": ["Check local server"],
            },
        },
    )

    diagnostics = build_platform_diagnostics(cli_config=CLISelectionConfig(), project_root=tmp_path)
    payload = diagnostics.to_dict()

    assert diagnostics.ok is True
    assert payload["preflight_gate"]["recorded"] is True
    assert payload["preflight_gate"]["status"] == "fail"
    assert payload["preflight_gate"]["errors"] == ["local LLM preflight failed"]
    assert payload["preflight_gate"]["recommendations"] == ["Check local server"]


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
