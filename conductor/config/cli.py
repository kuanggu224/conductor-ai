"""Agent CLI 扫描与绑定配置。"""

from __future__ import annotations

import json
import shutil
from dataclasses import dataclass, field
from pathlib import Path

from conductor.agents.profile import AgentProfile, build_default_agent_profiles
from conductor.config.defaults import CLI_CONFIG_PATH, CONFIG_DIR


AGENT_CLI_TOOLS: list[tuple[str, str]] = [
    ("codex", "Codex CLI"),
    ("claude", "Claude Code CLI"),
    ("qwen", "Qwen CLI"),
    ("opencode", "OpenCode CLI"),
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
    """扫描得到的 Agent CLI 工具信息。"""

    name: str
    label: str
    path: str | None
    available: bool


@dataclass(slots=True)
class CLISelectionConfig:
    """Agent CLI 选择配置。"""

    selected_cli_names: list[str] = field(default_factory=list)
    role_cli_bindings: dict[str, str | None] = field(default_factory=dict)
    codex_model: str = "gpt-5.4-mini"
    codex_reasoning_effort: str = "medium"


def _default_role_cli_bindings(profiles: list[AgentProfile] | None = None) -> dict[str, str | None]:
    """根据角色规格生成默认 CLI 绑定。"""
    bindings: dict[str, str | None] = {}
    for profile in profiles or build_default_agent_profiles():
        bindings[profile.role_name] = profile.default_cli_name
    return bindings


def discover_cli_tools() -> list[CLITool]:
    """扫描 PATH 中可用的 Agent CLI。"""
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
    """读取 Agent CLI 选择配置。"""
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
    )


def save_cli_selection_config(config: CLISelectionConfig, path: str | Path | None = None) -> Path:
    """保存 Agent CLI 选择配置。"""
    file_path = Path(path) if path is not None else CLI_CONFIG_PATH
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    with file_path.open("w", encoding="utf-8") as file:
        json.dump(
            {
                "selected_cli_names": config.selected_cli_names,
                "role_cli_bindings": config.role_cli_bindings,
                "codex_model": config.codex_model,
                "codex_reasoning_effort": config.codex_reasoning_effort,
            },
            file,
            ensure_ascii=False,
            indent=2,
        )
    return file_path


def build_cli_options(config: CLISelectionConfig | None = None) -> list[dict[str, str | bool | None]]:
    """构建带勾选状态的 Agent CLI 选项视图数据。"""
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
    profiles: list[AgentProfile] | None = None,
) -> list[dict[str, object]]:
    """构建角色到 Agent CLI 的绑定视图数据。"""
    runtime_config = config or load_cli_selection_config()
    discovered = discover_cli_tools()
    available_selected = [tool for tool in discovered if tool.available and tool.name in runtime_config.selected_cli_names]
    role_options: list[dict[str, object]] = []
    for profile in profiles or build_default_agent_profiles():
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
