from conductor.controller.engine import ConductorEngine
from conductor.config.cli import CLISelectionConfig
import json
from pathlib import Path
import shutil

project_root = Path(r"C:\99_self\conductor_claude_utf8_smoke2")
if project_root.exists():
    shutil.rmtree(project_root)
project_root.mkdir(parents=True, exist_ok=True)

cli_config = CLISelectionConfig(
    selected_cli_names=['claude'],
    role_cli_bindings={'designer':'claude','backend_engineer':None,'frontend_engineer':None,'tester':None},
)
engine = ConductorEngine(cli_selection_config=cli_config)
state = engine.create_project('为一个轻量任务中心输出需求设计和测试设计文档。', project_root=str(project_root))
state = engine.step_project(state.project.id)
print(json.dumps({
    'project_id': state.project.id,
    'artifacts': [
        {'id': a.id, 'kind': a.kind, 'source_backend': a.source_backend}
        for a in state.artifacts
    ],
    'recent_events_tail': state.recent_events[-8:],
}, ensure_ascii=False, indent=2))
