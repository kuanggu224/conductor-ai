"""执行范围配置。"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from enum import StrEnum
from pathlib import Path

from conductor.config.defaults import CONFIG_DIR

EXECUTION_CONFIG_PATH = CONFIG_DIR / "execution.config.json"


class RunProfile(StrEnum):
    """Preset runtime modes for controlling real CLI usage."""

    MOCK = "mock"
    STATIC_WEB = "static_web"
    FULLSTACK_WEB = "fullstack_web"
    API_MOCK = "api_mock"
    API_SQLITE = "api_sqlite"
    DESIGN_CLI_ONLY = "design_cli_only"
    CODE_CLI = "code_cli"
    FULL_CLI = "full_cli"


@dataclass(slots=True)
class RunProfileConfig:
    """Resolved execution behavior for one run profile."""

    profile: RunProfile
    cli_roles: list[str]
    require_real_design_outputs: bool = False
    require_real_code_outputs: bool = False
    enable_static_web_delivery: bool = False
    enable_fullstack_web_delivery: bool = False
    enable_api_mock_delivery: bool = False
    enable_api_sqlite_delivery: bool = False

    def uses_cli_for_role(self, role: str) -> bool:
        """Return whether the role should be bound to CLI in this profile."""
        return role in self.cli_roles


def resolve_run_profile(profile: str | RunProfile) -> RunProfileConfig:
    """Resolve a named run profile into concrete execution switches."""
    run_profile = profile if isinstance(profile, RunProfile) else RunProfile(profile)
    if run_profile == RunProfile.MOCK:
        return RunProfileConfig(profile=run_profile, cli_roles=[])
    if run_profile == RunProfile.STATIC_WEB:
        return RunProfileConfig(
            profile=run_profile,
            cli_roles=[],
            enable_static_web_delivery=True,
        )
    if run_profile == RunProfile.FULLSTACK_WEB:
        return RunProfileConfig(
            profile=run_profile,
            cli_roles=[],
            enable_fullstack_web_delivery=True,
        )
    if run_profile == RunProfile.API_MOCK:
        return RunProfileConfig(
            profile=run_profile,
            cli_roles=[],
            enable_api_mock_delivery=True,
        )
    if run_profile == RunProfile.API_SQLITE:
        return RunProfileConfig(
            profile=run_profile,
            cli_roles=[],
            enable_api_sqlite_delivery=True,
        )
    if run_profile == RunProfile.DESIGN_CLI_ONLY:
        return RunProfileConfig(
            profile=run_profile,
            cli_roles=["designer", "requirement_designer", "solution_designer"],
            require_real_design_outputs=True,
        )
    if run_profile == RunProfile.CODE_CLI:
        return RunProfileConfig(
            profile=run_profile,
            cli_roles=["backend_engineer", "frontend_engineer"],
            require_real_code_outputs=True,
        )
    return RunProfileConfig(
        profile=run_profile,
        cli_roles=["designer", "requirement_designer", "solution_designer", "backend_engineer", "frontend_engineer", "tester"],
        require_real_design_outputs=True,
        require_real_code_outputs=True,
    )


@dataclass(slots=True)
class ExecutionScopeConfig:
    """控制项目流程中启用哪些工作项。"""

    requirement_design_enabled: bool = True
    design_detail_enabled: bool = True
    design_collaboration_enabled: bool = True
    backend_development_enabled: bool = True
    frontend_development_enabled: bool = True
    testing_enabled: bool = True

    def is_workitem_kind_enabled(self, kind: str) -> bool:
        """判断指定 WorkItem kind 是否启用。"""
        if kind in {"requirement_spec", "design_overview"}:
            return self.requirement_design_enabled
        if kind in {"ui_design", "api_design", "test_design", "feature_slice_plan"}:
            return self.design_detail_enabled
        if kind in {"api_implementation", "data_implementation", "generic_implementation"}:
            return self.backend_development_enabled
        if kind == "ui_implementation":
            return self.frontend_development_enabled
        if kind in {"acceptance_check", "automated_test", "api_validation", "ui_validation"}:
            return self.testing_enabled
        return True


def load_execution_scope_config(path: str | Path = EXECUTION_CONFIG_PATH) -> ExecutionScopeConfig:
    """读取执行范围配置；不存在时返回默认值。"""
    config_path = Path(path)
    if not config_path.exists():
        return ExecutionScopeConfig()
    payload = json.loads(config_path.read_text(encoding="utf-8"))
    return ExecutionScopeConfig(
        requirement_design_enabled=bool(payload.get("requirement_design_enabled", True)),
        design_detail_enabled=bool(payload.get("design_detail_enabled", True)),
        design_collaboration_enabled=bool(payload.get("design_collaboration_enabled", True)),
        backend_development_enabled=bool(payload.get("backend_development_enabled", True)),
        frontend_development_enabled=bool(payload.get("frontend_development_enabled", True)),
        testing_enabled=bool(payload.get("testing_enabled", True)),
    )


def save_execution_scope_config(
    config: ExecutionScopeConfig,
    path: str | Path = EXECUTION_CONFIG_PATH,
) -> Path:
    """保存执行范围配置到本地 JSON 文件。"""
    config_path = Path(path)
    config_path.parent.mkdir(parents=True, exist_ok=True)
    config_path.write_text(
        json.dumps(asdict(config), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return config_path


__all__ = [
    "EXECUTION_CONFIG_PATH",
    "ExecutionScopeConfig",
    "RunProfile",
    "RunProfileConfig",
    "load_execution_scope_config",
    "resolve_run_profile",
    "save_execution_scope_config",
]
