import shutil
from conductor.controller.engine import ConductorEngine
from conductor.config.cli import CLISelectionConfig
from pathlib import Path
import shutil as sh

project_root = Path(r"C:\99_self\conductor_claude_utf8_debug2")
if project_root.exists():
    sh.rmtree(project_root)
project_root.mkdir(parents=True, exist_ok=True)
cli_config = CLISelectionConfig(selected_cli_names=['claude'], role_cli_bindings={'designer':'claude','backend_engineer':None,'frontend_engineer':None,'tester':None})
engine = ConductorEngine(cli_selection_config=cli_config)
state = engine.create_project('为一个轻量任务中心输出需求设计和测试设计文档。', project_root=str(project_root))
workitem = state.workitems[0]
agent = engine.registry.get_agent_by_role('designer')
prompt = engine.runner._build_document_prompt(workitem, agent)
path = shutil.which('claude')
cmd = engine.runner.agent_cli_executor._build_command(path, 'claude', prompt, 'documentation', state.project.project_root)
print(path)
print(cmd)
