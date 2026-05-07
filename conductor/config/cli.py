"""Agent CLI scan and binding configuration."""

from __future__ import annotations

import json
import shutil
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

from conductor.config.defaults import CLI_CONFIG_PATH, CONFIG_DIR

if TYPE_CHECKING:
    from conductor.agents.profile import AgentProfile


AGENT_CLI_TOOLS: list[tuple[str, str]] = [
    ("codex", "Codex CLI"),
    ("claude", "Claude Code CLI"),
    ("qwen", "Qwen CLI"),
    ("opencode", "OpenCode CLI"),
    ("aspirecode", "AspireCode CLI"),
    ("aider", "Aider CLI"),
    ("gemini", "Gemini CLI"),
]

ROLE_LABELS = {
    "designer": "产品/设计 Agent",
    "backend_engineer": "后端研发 Agent",
    "frontend_engineer": "前端研发 Agent",
    "tester": "测试 Agent",
}


@dataclass(slots=True)
class CLITool:
    """Information discovered for one CLI tool."""

    name: str
    label: str
    path: str | None
    available: bool


@dataclass(slots=True)
class CLISelectionConfig:
    """Agent CLI selection settings."""

    selected_cli_names: list[str] = field(default_factory=list)
    role_cli_bindings: dict[str, str | None] = field(default_factory=dict)
    codex_model: str = "gpt-5.4-mini"
    codex_reasoning_effort: str = "medium"
    aspirecode_model: str = "lmstudio-local/qwen3.6-35b-a3b"


def _default_role_cli_bindings(profiles: list["AgentProfile"] | None = None) -> dict[str, str | None]:
    """Build default role-to-CLI bindings."""
    if profiles is None:
        from conductor.agents.profile import build_default_agent_profiles

        profiles = build_default_agent_profiles()
    bindings: dict[str, str | None] = {}
    for profile in profiles:
        bindings[profile.role_name] = profile.default_cli_name
    return bindings


def discover_cli_tools() -> list[CLITool]:
    """Scan PATH for available agent CLI tools."""
    discovered: list[CLITool] = []
    for name, label in AGENT_CLI_TOOLS:
        path = shutil.which(name)
        discovered.append(
            CLITool(
                name=name,
                label=label,
                path=path,
                available=path is not None,
            )
        )
    return discovered


def load_cli_selection_config(path: str | Path | None = None) -> CLISelectionConfig:
    """Load CLI selection configuration from JSON."""
    file_path = Path(path) if path is not None else CLI_CONFIG_PATH
    if not file_path.exists():
        available = [tool.name for tool in discover_cli_tools() if tool.available]
        return CLISelectionConfig(
            selected_cli_names=available,
            role_cli_bindings=_default_role_cli_bindings(),
        )
    with file_path.open("r", encoding="utf-8") as file:
        payload = json.load(file)
    return CLISelectionConfig(
        selected_cli_names=list(payload.get("selected_cli_names", [])),
        role_cli_bindings={
            **_default_role_cli_bindings(),
            **dict(payload.get("role_cli_bindings", {})),
        },
        codex_model=str(payload.get("codex_model", "gpt-5.4-mini")),
        codex_reasoning_effort=str(payload.get("codex_reasoning_effort", "medium")),
        aspirecode_model=str(payload.get("aspirecode_model", "lmstudio-local/qwen3.6-35b-a3b")),
    )


def save_cli_selection_config(config: CLISelectionConfig, path: str | Path | None = None) -> Path:
    """Persist CLI selection configuration."""
    file_path = Path(path) if path is not None else CLI_CONFIG_PATH
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    with file_path.open("w", encoding="utf-8") as file:
        json.dump(
            {
                "selected_cli_names": config.selected_cli_names,
                "role_cli_bindings": config.role_cli_bindings,
                "codex_model": config.codex_model,
                "codex_reasoning_effort": config.codex_reasoning_effort,
                "aspirecode_model": config.aspirecode_model,
            },
            file,
            ensure_ascii=False,
            indent=2,
        )
    return file_path


def build_cli_options(config: CLISelectionConfig | None = None) -> list[dict[str, str | bool | None]]:
    """Build the options payload for CLI selection views."""
    runtime_config = config or load_cli_selection_config()
    selected = set(runtime_config.selected_cli_names)
    options: list[dict[str, str | bool | None]] = []
    for tool in discover_cli_tools():
        options.append(
            {
                "name": tool.name,
                "label": tool.label,
                "path": tool.path,
                "available": tool.available,
                "selected": tool.name in selected,
            }
        )
    return options


def build_role_cli_binding_options(
    config: CLISelectionConfig | None = None,
    profiles: list["AgentProfile"] | None = None,
) -> list[dict[str, object]]:
    """Build role-to-CLI binding view data."""
    runtime_config = config or load_cli_selection_config()
    if profiles is None:
        from conductor.agents.profile import build_default_agent_profiles

        profiles = build_default_agent_profiles()
    discovered = discover_cli_tools()
    available_selected = [tool for tool in discovered if tool.available and tool.name in runtime_config.selected_cli_names]
    role_options: list[dict[str, object]] = []
    for profile in profiles:
        selected_cli = runtime_config.role_cli_bindings.get(profile.role_name, profile.default_cli_name)
        role_options.append(
            {
                "role_name": profile.role_name,
                "role_label": ROLE_LABELS.get(profile.role_name, profile.role_name),
                "mission": profile.mission,
                "selected_cli": selected_cli or "",
                "choices": [
                    {"name": "", "label": "不绑定 CLI"}
                ]
                + [
                    {"name": tool.name, "label": f"{tool.label} ({tool.name})"}
                    for tool in available_selected
                ],
            }
        )
    return role_options
