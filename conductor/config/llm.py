"""LLM backend 配置构建。"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path

from conductor.agents.llm import (
    HybridLLMBackend,
    LLMHTTPConfig,
    LocalModelHTTPBackend,
    OpenAICompatibleCloudLLMBackend,
)
from conductor.config.defaults import CONFIG_DIR, LLM_CONFIG_PATH

DEFAULT_RUNNER_ALLOWED_ROLES = ["designer", "backend_engineer", "tester"]
DEFAULT_RUNNER_ALLOWED_KINDS = [
    "design_overview",
    "feature_slice_plan",
    "api_design",
    "test_design",
    "generic_implementation",
    "api_implementation",
    "data_implementation",
    "acceptance_check",
    "automated_test",
    "api_validation",
]


def _env_flag(name: str, default: bool = False) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _env_float(name: str, default: float) -> float:
    value = os.getenv(name)
    if value is None:
        return default
    return float(value)


@dataclass(slots=True)
class LLMRuntimeConfig:
    """运行时 LLM 配置聚合。"""

    local: LLMHTTPConfig
    cloud: LLMHTTPConfig
    usage: "LLMUsagePolicy"
    pricing: "LLMPricingConfig" = field(default_factory=lambda: LLMPricingConfig())


@dataclass(slots=True)
class LLMUsagePolicy:
    """LLM 使用策略。"""

    runner_enabled: bool = False
    runner_allowed_roles: list[str] = None
    runner_allowed_kinds: list[str] = None
    preferred_backend: str = "cloud"

    def __post_init__(self) -> None:
        """补齐默认 allowlist。"""
        if self.runner_allowed_roles is None:
            self.runner_allowed_roles = list(DEFAULT_RUNNER_ALLOWED_ROLES)
        if self.runner_allowed_kinds is None:
            self.runner_allowed_kinds = list(DEFAULT_RUNNER_ALLOWED_KINDS)


@dataclass(slots=True)
class LLMPricingConfig:
    """Optional LLM pricing table used for manifest cost estimation."""

    currency: str = "USD"
    per_million_tokens: dict[str, dict[str, float]] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class LLMProviderPreset:
    """Reusable OpenAI-compatible endpoint preset."""

    id: str
    label: str
    backend: str
    base_url: str
    model_name: str
    timeout_seconds: float = 30.0


LLM_PROVIDER_PRESETS = [
    LLMProviderPreset(
        id="openai",
        label="OpenAI Compatible",
        backend="cloud",
        base_url="https://api.openai.com/v1",
        model_name="gpt-demo-model",
        timeout_seconds=30.0,
    ),
    LLMProviderPreset(
        id="jiutian",
        label="Jiutian",
        backend="cloud",
        base_url="https://jiutian.10086.cn/largemodel/moma/api/v3",
        model_name="jiutian-lan-comv3",
        timeout_seconds=120.0,
    ),
    LLMProviderPreset(
        id="lmstudio",
        label="LM Studio",
        backend="local",
        base_url="http://127.0.0.1:1234/v1",
        model_name="local-model",
        timeout_seconds=180.0,
    ),
]


def list_llm_provider_presets(backend: str | None = None) -> list[LLMProviderPreset]:
    """Return provider presets, optionally filtered by backend."""
    if backend is None:
        return list(LLM_PROVIDER_PRESETS)
    return [preset for preset in LLM_PROVIDER_PRESETS if preset.backend == backend]


def get_llm_provider_preset(preset_id: str | None) -> LLMProviderPreset | None:
    """Find one provider preset by id."""
    if not preset_id:
        return None
    for preset in LLM_PROVIDER_PRESETS:
        if preset.id == preset_id:
            return preset
    return None


def _read_file_config(path: str | Path | None = None) -> dict:
    file_path = Path(path) if path is not None else LLM_CONFIG_PATH
    if not file_path.exists():
        return {}
    with file_path.open("r", encoding="utf-8") as file:
        return json.load(file)


def _resolve_value(file_section: dict, env_name: str, default):
    value = os.getenv(env_name)
    if value is not None:
        return value
    return file_section.get(env_name.lower().removeprefix("conductor_"), default)


def _resolve_flag(file_section: dict, env_name: str, default: bool) -> bool:
    value = os.getenv(env_name)
    if value is not None:
        return _env_flag(env_name, default)
    file_value = file_section.get(env_name.lower().removeprefix("conductor_"))
    if file_value is None:
        return default
    return bool(file_value)


def _resolve_float(file_section: dict, env_name: str, default: float) -> float:
    value = os.getenv(env_name)
    if value is not None:
        return _env_float(env_name, default)
    file_value = file_section.get(env_name.lower().removeprefix("conductor_"))
    if file_value is None:
        return default
    return float(file_value)


def _pricing_table(value: object) -> dict[str, dict[str, float]]:
    """Normalize a model pricing table from local config."""
    if not isinstance(value, dict):
        return {}
    table: dict[str, dict[str, float]] = {}
    for model_name, rates in value.items():
        if not isinstance(rates, dict):
            continue
        normalized_rates: dict[str, float] = {}
        for token_type, rate in rates.items():
            try:
                normalized_rates[str(token_type)] = float(rate)
            except (TypeError, ValueError):
                continue
        if normalized_rates:
            table[str(model_name)] = normalized_rates
    return table


def load_llm_runtime_config(path: str | Path | None = None) -> LLMRuntimeConfig:
    """从本地配置文件和环境变量读取本地/云端 LLM 配置。"""
    file_config = _read_file_config(path)
    local_section = file_config.get("local", {})
    cloud_section = file_config.get("cloud", {})
    usage_section = file_config.get("usage", {})
    pricing_section = file_config.get("pricing", {})
    return LLMRuntimeConfig(
        local=LLMHTTPConfig(
            base_url=_resolve_value(local_section, "CONDUCTOR_LOCAL_LLM_BASE_URL", "http://127.0.0.1:11434/v1"),
            model_name=_resolve_value(local_section, "CONDUCTOR_LOCAL_LLM_MODEL", "local-demo-model"),
            api_key=_resolve_value(local_section, "CONDUCTOR_LOCAL_LLM_API_KEY", None),
            timeout_seconds=_resolve_float(local_section, "CONDUCTOR_LOCAL_LLM_TIMEOUT", 30.0),
            reasoning_effort=_resolve_value(local_section, "CONDUCTOR_LOCAL_LLM_REASONING_EFFORT", None),
            enabled=_resolve_flag(local_section, "CONDUCTOR_LOCAL_LLM_ENABLED", False),
        ),
        cloud=LLMHTTPConfig(
            base_url=_resolve_value(cloud_section, "CONDUCTOR_CLOUD_LLM_BASE_URL", "https://api.openai.com/v1"),
            model_name=_resolve_value(cloud_section, "CONDUCTOR_CLOUD_LLM_MODEL", "gpt-demo-model"),
            api_key=_resolve_value(cloud_section, "CONDUCTOR_CLOUD_LLM_API_KEY", None),
            timeout_seconds=_resolve_float(cloud_section, "CONDUCTOR_CLOUD_LLM_TIMEOUT", 30.0),
            reasoning_effort=_resolve_value(cloud_section, "CONDUCTOR_CLOUD_LLM_REASONING_EFFORT", None),
            enabled=_resolve_flag(cloud_section, "CONDUCTOR_CLOUD_LLM_ENABLED", False),
        ),
        usage=LLMUsagePolicy(
            runner_enabled=bool(usage_section.get("runner_enabled", False)),
            runner_allowed_roles=list(usage_section.get("runner_allowed_roles", DEFAULT_RUNNER_ALLOWED_ROLES)),
            runner_allowed_kinds=list(usage_section.get("runner_allowed_kinds", DEFAULT_RUNNER_ALLOWED_KINDS)),
            preferred_backend=str(usage_section.get("preferred_backend", "cloud")),
        ),
        pricing=LLMPricingConfig(
            currency=str(pricing_section.get("currency", "USD")) if isinstance(pricing_section, dict) else "USD",
            per_million_tokens=_pricing_table(
                pricing_section.get("per_million_tokens", {}) if isinstance(pricing_section, dict) else {}
            ),
        ),
    )


def build_default_hybrid_llm_backend(config: LLMRuntimeConfig | None = None) -> HybridLLMBackend:
    """构建默认 HybridLLMBackend。"""
    runtime_config = config or load_llm_runtime_config()
    return HybridLLMBackend(
        local_backend=LocalModelHTTPBackend(runtime_config.local),
        cloud_backend=OpenAICompatibleCloudLLMBackend(runtime_config.cloud),
    )


def save_llm_runtime_config(config: LLMRuntimeConfig, path: str | Path | None = None) -> Path:
    """保存本地 LLM 配置文件。"""
    file_path = Path(path) if path is not None else LLM_CONFIG_PATH
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    payload = {
        "local": {
            "local_llm_base_url": config.local.base_url,
            "local_llm_model": config.local.model_name,
            "local_llm_api_key": config.local.api_key,
            "local_llm_timeout": config.local.timeout_seconds,
            "local_llm_reasoning_effort": config.local.reasoning_effort,
            "local_llm_enabled": config.local.enabled,
        },
        "cloud": {
            "cloud_llm_base_url": config.cloud.base_url,
            "cloud_llm_model": config.cloud.model_name,
            "cloud_llm_api_key": config.cloud.api_key,
            "cloud_llm_timeout": config.cloud.timeout_seconds,
            "cloud_llm_reasoning_effort": config.cloud.reasoning_effort,
            "cloud_llm_enabled": config.cloud.enabled,
        },
        "usage": {
            "runner_enabled": config.usage.runner_enabled,
            "runner_allowed_roles": config.usage.runner_allowed_roles,
            "runner_allowed_kinds": config.usage.runner_allowed_kinds,
            "preferred_backend": config.usage.preferred_backend,
        },
        "pricing": {
            "currency": config.pricing.currency,
            "per_million_tokens": config.pricing.per_million_tokens,
        },
    }
    with file_path.open("w", encoding="utf-8") as file:
        json.dump(payload, file, ensure_ascii=False, indent=2)
    return file_path
