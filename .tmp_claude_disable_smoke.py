from conductor.controller.engine import ConductorEngine
from conductor.config.cli import CLISelectionConfig
from pathlib import Path
import shutil, json

project_root = Path(r"C:\99_self\conductor_claude_disable_smoke")
if project_root.exists():
    shutil.rmtree(project_root)
project_root.mkdir(parents=True, exist_ok=True)
cli_config = CLISelectionConfig(selected_cli_names=['claude'], role_cli_bindings={'designer':'claude','backend_engineer':None,'frontend_engineer':None,'tester':None})
engine = ConductorEngine(cli_selection_config=cli_config)
state = engine.create_project('实现一个包含 API、UI 和测试的轻量任务中心。', project_root=str(project_root))
state = engine.step_project(state.project.id)
state = engine.step_project(state.project.id)
state = engine.step_project(state.project.id)
for event in state.recent_events[-20:]:
    print(event)
