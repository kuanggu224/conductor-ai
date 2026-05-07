"""Platform diagnostics for CLI-backed Conductor runs."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from pathlib import Path

from conductor.config.cli import CLISelectionConfig, discover_cli_tools, load_cli_selection_config
from conductor.config.defaults import CLI_CONFIG_PATH, LLM_CONFIG_PATH
from conductor.config.execution import EXECUTION_CONFIG_PATH
from conductor.config.system import SYSTEM_CONFIG_PATH


@dataclass(slots=True)
class RoleBindingDiagnostic:
    """Health information for one Agent role CLI binding."""

    role: str
    cli_name: str
    available: bool
    selected: bool
    status: str
    message: str


@dataclass(slots=True)
class PlatformDiagnostics:
    """Serializable platform health snapshot."""

    ok: bool
    project_root: str
    config_paths: dict[str, str]
    selected_cli_names: list[str]
    available_cli_names: list[str]
    role_bindings: list[RoleBindingDiagnostic] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, object]:
        """Return a JSON-serializable dictionary."""
        return asdict(self)


def build_platform_diagnostics(
    cli_config: CLISelectionConfig | None = None,
    project_root: str | Path | None = None,
) -> PlatformDiagnostics:
    """Build a diagnostic snapshot for the current local platform setup."""
    runtime_cli_config = cli_config or load_cli_selection_config()
    tools = discover_cli_tools()
    available_by_name = {tool.name: tool.available for tool in tools}
    available_cli_names = [tool.name for tool in tools if tool.available]
    selected_cli_names = list(runtime_cli_config.selected_cli_names)
    role_bindings = [
        _diagnose_role_binding(
            role=role,
            cli_name=cli_name or "",
            selected_cli_names=selected_cli_names,
            available_by_name=available_by_name,
        )
        for role, cli_name in sorted(runtime_cli_config.role_cli_bindings.items())
    ]
    warnings = _build_warnings(selected_cli_names, available_by_name, role_bindings)
    return PlatformDiagnostics(
        ok=not warnings,
        project_root=str(Path(project_root or Path.cwd()).expanduser().resolve()),
        config_paths={
            "cli": str(CLI_CONFIG_PATH),
            "system": str(SYSTEM_CONFIG_PATH),
            "execution": str(EXECUTION_CONFIG_PATH),
            "llm": str(LLM_CONFIG_PATH),
        },
        selected_cli_names=selected_cli_names,
        available_cli_names=available_cli_names,
        role_bindings=role_bindings,
        warnings=warnings,
    )


def _diagnose_role_binding(
    role: str,
    cli_name: str,
    selected_cli_names: list[str],
    available_by_name: dict[str, bool],
) -> RoleBindingDiagnostic:
    if not cli_name:
        return RoleBindingDiagnostic(
            role=role,
            cli_name="",
            available=False,
            selected=False,
            status="unbound",
            message="该角色未绑定 CLI，会走 LLM 或 mock fallback。",
        )
    available = available_by_name.get(cli_name, False)
    selected = cli_name in selected_cli_names
    if not selected:
        return RoleBindingDiagnostic(
            role=role,
            cli_name=cli_name,
            available=available,
            selected=False,
            status="not_selected",
            message=f"{role} 绑定了 {cli_name}，但该 CLI 未被选中启用。",
        )
    if not available:
        return RoleBindingDiagnostic(
            role=role,
            cli_name=cli_name,
            available=False,
            selected=True,
            status="missing",
            message=f"{role} 绑定了 {cli_name}，但当前 PATH 未发现该 CLI。",
        )
    return RoleBindingDiagnostic(
        role=role,
        cli_name=cli_name,
        available=True,
        selected=True,
        status="ready",
        message=f"{role} 将使用 {cli_name} CLI。",
    )


def _build_warnings(
    selected_cli_names: list[str],
    available_by_name: dict[str, bool],
    role_bindings: list[RoleBindingDiagnostic],
) -> list[str]:
    warnings: list[str] = []
    for cli_name in selected_cli_names:
        if not available_by_name.get(cli_name, False):
            warnings.append(f"已选 CLI `{cli_name}` 当前不可用，请检查安装或 PATH。")
    for binding in role_bindings:
        if binding.status in {"not_selected", "missing"}:
            warnings.append(binding.message)
    return warnings


__all__ = [
    "PlatformDiagnostics",
    "RoleBindingDiagnostic",
    "build_platform_diagnostics",
]
