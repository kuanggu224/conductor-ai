"""Configuration package for Conductor."""

from __future__ import annotations

from importlib import import_module

from conductor.config.defaults import CLI_CONFIG_PATH, CONFIG_DIR, LLM_CONFIG_PATH

_EXPORTS = {
    "AgentConfig": ("conductor.config.system", "AgentConfig"),
    "AgentsConfig": ("conductor.config.system", "AgentsConfig"),
    "CollaborationConfig": ("conductor.config.system", "CollaborationConfig"),
    "LLMProviderPreset": ("conductor.config.llm", "LLMProviderPreset"),
    "get_llm_provider_preset": ("conductor.config.llm", "get_llm_provider_preset"),
    "list_llm_provider_presets": ("conductor.config.llm", "list_llm_provider_presets"),
    "PlannerConfig": ("conductor.config.system", "PlannerConfig"),
    "RoleMappingConfig": ("conductor.config.system", "RoleMappingConfig"),
    "SystemConfig": ("conductor.config.system", "SystemConfig"),
    "WorkflowConfig": ("conductor.config.system", "WorkflowConfig"),
}

__all__ = ["CLI_CONFIG_PATH", "CONFIG_DIR", "LLM_CONFIG_PATH", *sorted(_EXPORTS)]


def __getattr__(name: str):
    if name not in _EXPORTS:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    module_name, attr_name = _EXPORTS[name]
    value = getattr(import_module(module_name), attr_name)
    globals()[name] = value
    return value


def __dir__() -> list[str]:
    return sorted({*globals(), *_EXPORTS})
