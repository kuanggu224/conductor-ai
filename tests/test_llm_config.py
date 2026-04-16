"""LLM 配置层测试。"""

from conductor.config.llm import (
    LLMRuntimeConfig,
    build_default_hybrid_llm_backend,
    load_llm_runtime_config,
    save_llm_runtime_config,
)
from conductor.agents.llm import LLMHTTPConfig


def test_load_llm_runtime_config_reads_environment(monkeypatch) -> None:
    monkeypatch.setenv("CONDUCTOR_LOCAL_LLM_ENABLED", "true")
    monkeypatch.setenv("CONDUCTOR_LOCAL_LLM_MODEL", "qwen-local")
    monkeypatch.setenv("CONDUCTOR_CLOUD_LLM_ENABLED", "true")
    monkeypatch.setenv("CONDUCTOR_CLOUD_LLM_MODEL", "gpt-cloud")

    config = load_llm_runtime_config()

    assert config.local.enabled is True
    assert config.local.model_name == "qwen-local"
    assert config.cloud.enabled is True
    assert config.cloud.model_name == "gpt-cloud"


def test_build_default_hybrid_llm_backend_uses_runtime_config(monkeypatch) -> None:
    monkeypatch.setenv("CONDUCTOR_LOCAL_LLM_MODEL", "qwen-local")
    monkeypatch.setenv("CONDUCTOR_CLOUD_LLM_MODEL", "gpt-cloud")

    hybrid = build_default_hybrid_llm_backend()

    assert hybrid.local_backend.model_name == "qwen-local"
    assert hybrid.cloud_backend.model_name == "gpt-cloud"


def test_load_llm_runtime_config_reads_local_file(tmp_path) -> None:
    config_path = tmp_path / "llm.config.json"
    config_path.write_text(
        """
{
  "local": {
    "local_llm_base_url": "http://127.0.0.1:2345/v1",
    "local_llm_model": "qwen-local",
    "local_llm_enabled": true
  },
  "cloud": {
    "cloud_llm_base_url": "https://ai.cdn.ad/v1",
    "cloud_llm_model": "DeepSeek-R1-0528",
    "cloud_llm_api_key": "sk-demo",
    "cloud_llm_enabled": true
  }
}
        """.strip(),
        encoding="utf-8",
    )

    config = load_llm_runtime_config(config_path)

    assert config.local.base_url == "http://127.0.0.1:2345/v1"
    assert config.local.enabled is True
    assert config.cloud.model_name == "DeepSeek-R1-0528"
    assert config.cloud.api_key == "sk-demo"


def test_save_llm_runtime_config_writes_local_file(tmp_path) -> None:
    config = LLMRuntimeConfig(
        local=LLMHTTPConfig(
            base_url="http://127.0.0.1:11434/v1",
            model_name="qwen-local",
            api_key=None,
            timeout_seconds=15.0,
            enabled=True,
        ),
        cloud=LLMHTTPConfig(
            base_url="https://ai.cdn.ad/v1",
            model_name="DeepSeek-R1-0528",
            api_key="sk-demo",
            timeout_seconds=30.0,
            enabled=True,
        ),
        usage=load_llm_runtime_config(tmp_path / "missing.json").usage,
    )

    path = save_llm_runtime_config(config, tmp_path / "llm.config.json")
    loaded = load_llm_runtime_config(path)

    assert path.exists()
    assert loaded.cloud.base_url == "https://ai.cdn.ad/v1"
    assert loaded.cloud.model_name == "DeepSeek-R1-0528"


def test_load_llm_runtime_config_reads_usage_policy(tmp_path) -> None:
    config_path = tmp_path / "llm.config.json"
    config_path.write_text(
        """
{
  "usage": {
    "runner_enabled": true,
    "runner_allowed_roles": ["designer"],
    "runner_allowed_kinds": ["design_overview"],
    "preferred_backend": "cloud"
  }
}
        """.strip(),
        encoding="utf-8",
    )

    config = load_llm_runtime_config(config_path)

    assert config.usage.runner_enabled is True
    assert config.usage.runner_allowed_kinds == ["design_overview"]


def test_default_usage_policy_allows_all_document_agents() -> None:
    config = load_llm_runtime_config("missing-config.json")

    assert config.usage.runner_allowed_roles == [
        "designer",
        "backend_engineer",
        "frontend_engineer",
        "tester",
    ]
    assert "api_implementation" in config.usage.runner_allowed_kinds
    assert "ui_validation" in config.usage.runner_allowed_kinds
