"""Board 配置页面测试。"""

from pathlib import Path

from fastapi.testclient import TestClient

from app import board
from conductor.agents.llm import LLMHTTPConfig
from conductor.config.cli import load_cli_selection_config
from conductor.config.execution import load_execution_scope_config
from conductor.config.llm import LLMRuntimeConfig, LLMUsagePolicy, load_llm_runtime_config
from conductor.requirement_benchmark import RequirementLLMPreflightResult


def test_llm_settings_page_renders() -> None:
    client = TestClient(board.app)

    response = client.get("/settings/llm")

    assert response.status_code == 200
    assert "LLM 设置" in response.text
    assert "Jiutian" in response.text


def test_save_llm_settings_writes_config(monkeypatch, tmp_path) -> None:
    config_path = tmp_path / "llm.config.json"
    monkeypatch.setattr("conductor.config.llm.LLM_CONFIG_PATH", config_path)
    monkeypatch.setattr("conductor.config.llm.CONFIG_DIR", tmp_path)
    client = TestClient(board.app)

    response = client.post(
        "/settings/llm",
        data={
            "local_base_url": "http://127.0.0.1:11434/v1",
            "local_model": "qwen-local",
            "local_timeout": "15",
            "cloud_base_url": "https://ai.cdn.ad/v1",
            "cloud_model": "DeepSeek-R1-0528",
            "cloud_api_key": "sk-demo",
            "cloud_timeout": "30",
            "cloud_enabled": "on",
        },
        follow_redirects=False,
    )

    saved = load_llm_runtime_config(config_path)
    assert response.status_code == 303
    assert Path(config_path).exists()
    assert saved.cloud.model_name == "DeepSeek-R1-0528"
    assert saved.cloud.enabled is True


def test_llm_settings_api_exposes_provider_presets() -> None:
    client = TestClient(board.app)

    response = client.get("/api/settings/llm")

    assert response.status_code == 200
    payload = response.json()
    assert "provider_presets" in payload
    assert any(item["id"] == "jiutian" for item in payload["provider_presets"])


def test_llm_settings_api_redacts_existing_api_keys(monkeypatch) -> None:
    monkeypatch.setattr(board, "load_llm_runtime_config", lambda: build_llm_config_with_keys())
    client = TestClient(board.app)

    response = client.get("/api/settings/llm")

    payload = response.json()
    assert response.status_code == 200
    assert "sk-cloud-secret" not in response.text
    assert payload["cloud"]["api_key"] is None
    assert payload["cloud"]["api_key_present"] is True
    assert payload["local"]["api_key_present"] is True


def test_llm_settings_page_redacts_existing_api_keys(monkeypatch) -> None:
    monkeypatch.setattr(board, "load_llm_runtime_config", lambda: build_llm_config_with_keys())
    client = TestClient(board.app)

    response = client.get("/settings/llm")

    assert response.status_code == 200
    assert "sk-cloud-secret" not in response.text
    assert "已配置，留空保留原 key" in response.text


def test_save_llm_settings_api_can_apply_cloud_preset(monkeypatch) -> None:
    captured = {}
    monkeypatch.setattr(board, "save_llm_runtime_config", lambda config: captured.setdefault("config", config))
    monkeypatch.setattr(board, "refresh_engine_llm_backend", lambda: None)
    client = TestClient(board.app)

    response = client.post(
        "/api/settings/llm",
        json={
            "cloud": {
                "preset_id": "jiutian",
                "base_url": "https://wrong.example/v1",
                "model_name": "wrong-model",
                "api_key": "sk-demo",
                "enabled": True,
            },
            "usage": {"runner_enabled": True, "preferred_backend": "cloud"},
        },
    )

    saved = captured["config"]
    assert response.status_code == 200
    assert saved.cloud.base_url == "https://jiutian.10086.cn/largemodel/moma/api/v3"
    assert saved.cloud.model_name == "jiutian-lan-comv3"
    assert saved.cloud.timeout_seconds == 120.0
    assert saved.cloud.api_key == "sk-demo"


def test_save_llm_settings_api_preserves_existing_api_key_when_blank(monkeypatch) -> None:
    captured = {}
    monkeypatch.setattr(board, "load_llm_runtime_config", lambda: build_llm_config_with_keys())
    monkeypatch.setattr(board, "save_llm_runtime_config", lambda config: captured.setdefault("config", config))
    monkeypatch.setattr(board, "refresh_engine_llm_backend", lambda: None)
    client = TestClient(board.app)

    response = client.post(
        "/api/settings/llm",
        json={
            "cloud": {
                "base_url": "https://example.com/v1",
                "model_name": "cloud-model",
                "api_key": "",
                "enabled": True,
            },
        },
    )

    assert response.status_code == 200
    assert captured["config"].cloud.api_key == "sk-cloud-secret"


def test_llm_settings_preflight_api_uses_draft_preset_without_exposing_key(monkeypatch) -> None:
    calls = []
    monkeypatch.setattr(board, "load_llm_runtime_config", lambda: build_llm_config_with_keys())

    def fake_preflight(*, backend, config, output_dir):
        calls.append((backend, config.base_url, config.model_name, config.api_key, output_dir))
        return RequirementLLMPreflightResult(
            backend=backend,
            success=True,
            model=config.model_name,
            base_url=config.base_url,
            duration_ms=12,
            error="",
            content="conductor-requirement-preflight-ok",
        )

    monkeypatch.setattr(board, "run_requirement_llm_preflight", fake_preflight)
    client = TestClient(board.app)

    response = client.post(
        "/api/settings/llm/preflight",
        json={
            "backend": "cloud",
            "cloud": {
                "preset_id": "jiutian",
                "api_key": "",
            },
        },
    )

    payload = response.json()
    assert response.status_code == 200
    assert payload["success"] is True
    assert payload["model"] == "jiutian-lan-comv3"
    assert "sk-cloud-secret" not in response.text
    assert calls[0][0] == "cloud"
    assert calls[0][1] == "https://jiutian.10086.cn/largemodel/moma/api/v3"
    assert calls[0][2] == "jiutian-lan-comv3"
    assert calls[0][3] == "sk-cloud-secret"


def test_llm_settings_preflight_api_rejects_unknown_backend() -> None:
    client = TestClient(board.app)

    response = client.post("/api/settings/llm/preflight", json={"backend": "edge"})

    assert response.status_code == 422


def test_save_llm_settings_form_preserves_existing_api_key_when_blank(monkeypatch) -> None:
    captured = {}
    monkeypatch.setattr(board, "load_llm_runtime_config", lambda: build_llm_config_with_keys())
    monkeypatch.setattr(board, "save_llm_runtime_config", lambda config: captured.setdefault("config", config))
    monkeypatch.setattr(board, "refresh_engine_llm_backend", lambda: None)
    client = TestClient(board.app)

    response = client.post(
        "/settings/llm",
        data={
            "cloud_base_url": "https://example.com/v1",
            "cloud_model": "cloud-model",
            "cloud_timeout": "45",
            "cloud_enabled": "on",
        },
        follow_redirects=False,
    )

    assert response.status_code == 303
    assert captured["config"].cloud.api_key == "sk-cloud-secret"


def test_execution_settings_page_renders() -> None:
    client = TestClient(board.app)

    response = client.get("/settings/execution")

    assert response.status_code == 200
    assert "执行范围设置" in response.text


def test_cli_settings_page_renders() -> None:
    client = TestClient(board.app)

    response = client.get("/settings/cli")

    assert response.status_code == 200
    assert "Agent CLI 设置" in response.text


def test_save_cli_settings_writes_config(monkeypatch, tmp_path) -> None:
    config_path = tmp_path / "cli.config.json"
    monkeypatch.setattr(board, "save_cli_selection_config", lambda config: save_cli_config_for_test(config, config_path))
    monkeypatch.setattr(board, "load_cli_selection_config", lambda: load_cli_selection_config(config_path))
    monkeypatch.setattr(board, "build_cli_options", lambda: [])
    client = TestClient(board.app)

    response = client.post(
        "/settings/cli",
        data={
            "selected_cli_names": ["codex", "claude", "qwen"],
            "role_cli_binding_designer": "",
            "role_cli_binding_backend_engineer": "codex",
            "role_cli_binding_frontend_engineer": "claude",
            "role_cli_binding_tester": "qwen",
        },
        follow_redirects=False,
    )

    saved = load_cli_selection_config(config_path)
    assert response.status_code == 303
    assert Path(config_path).exists()
    assert saved.selected_cli_names == ["codex", "claude", "qwen"]
    assert saved.role_cli_bindings["tester"] == "qwen"
    assert saved.role_cli_bindings["backend_engineer"] == "codex"


def test_save_execution_settings_writes_config(monkeypatch, tmp_path) -> None:
    config_path = tmp_path / "execution.config.json"
    monkeypatch.setattr(board, "save_execution_scope_config", lambda config: save_config_for_test(config, config_path))
    monkeypatch.setattr(board, "load_execution_scope_config", lambda: load_execution_scope_config(config_path))
    client = TestClient(board.app)

    response = client.post(
        "/settings/execution",
        data={
            "requirement_design_enabled": "on",
            "backend_development_enabled": "on",
            "testing_enabled": "on",
        },
        follow_redirects=False,
    )

    saved = load_execution_scope_config(config_path)
    assert response.status_code == 303
    assert Path(config_path).exists()
    assert saved.requirement_design_enabled is True
    assert saved.design_detail_enabled is False
    assert saved.design_collaboration_enabled is False
    assert saved.backend_development_enabled is True
    assert saved.frontend_development_enabled is False
    assert saved.testing_enabled is True


def save_config_for_test(config, config_path):
    from conductor.config.execution import save_execution_scope_config

    return save_execution_scope_config(config, config_path)


def save_cli_config_for_test(config, config_path):
    from conductor.config.cli import save_cli_selection_config

    return save_cli_selection_config(config, config_path)


def build_llm_config_with_keys() -> LLMRuntimeConfig:
    return LLMRuntimeConfig(
        local=LLMHTTPConfig(
            base_url="http://127.0.0.1:1234/v1",
            model_name="local-model",
            api_key="sk-local-secret",
            enabled=True,
        ),
        cloud=LLMHTTPConfig(
            base_url="https://jiutian.10086.cn/largemodel/moma/api/v3",
            model_name="jiutian-lan-comv3",
            api_key="sk-cloud-secret",
            enabled=True,
        ),
        usage=LLMUsagePolicy(runner_enabled=True),
    )


def test_folder_picker_page_renders(tmp_path) -> None:
    child = tmp_path / "child"
    child.mkdir()
    client = TestClient(board.app)

    response = client.get("/folders/pick", params={"current_path": str(tmp_path), "requirement": "实现最小功能"})

    assert response.status_code == 200
    assert "选择项目目录" in response.text
    assert str(child) in response.text
