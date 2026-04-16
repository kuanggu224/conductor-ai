"""执行范围配置。"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from pathlib import Path

from conductor.config.defaults import CONFIG_DIR

EXECUTION_CONFIG_PATH = CONFIG_DIR / "execution.config.json"


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
        if kind == "design_overview":
            return self.requirement_design_enabled
        if kind in {"ui_design", "api_design", "test_design"}:
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
