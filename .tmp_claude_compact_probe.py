from conductor.execution.runner import Runner
from conductor.state.store import InMemoryStateStore
from conductor.domain.models import WorkItem
from conductor.agents.registry import AgentRegistry
from conductor.config.cli import CLISelectionConfig
from conductor.agents.cli_executor import AgentCLIExecutor
import json

runner = Runner(state_store=InMemoryStateStore(), cli_selection_config=CLISelectionConfig())
registry = AgentRegistry()
agent = registry.get_agent_by_role('designer')
workitem = WorkItem(id='workitem-001', description='梳理需求并形成总体设计：为一个轻量任务中心输出需求设计和测试设计文档。', stage='design', kind='design_overview', acceptance_criteria=['输出设计要点','明确下一阶段实现边界'])
prompt = runner._build_compact_claude_document_prompt(workitem, agent)
print(prompt)
executor = AgentCLIExecutor(cli_selection_config=CLISelectionConfig(selected_cli_names=['claude'], role_cli_bindings={'designer':'claude'}))
execution = executor.execute(agent, prompt, execution_mode='documentation', timeout_seconds=180, working_directory=r'C:\99_self\conductor\conductor-ai')
print(json.dumps({'success': execution.result.success, 'exit_code': execution.result.exit_code, 'stdout': execution.result.stdout[:3000], 'stderr': execution.result.stderr[:1000]}, ensure_ascii=False, indent=2))
