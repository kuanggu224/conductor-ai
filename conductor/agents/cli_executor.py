"""Agent CLI 执行器。"""

from __future__ import annotations

import shutil
from dataclasses import dataclass
from pathlib import Path
import os

from conductor.agents.agent import Agent
from conductor.config.cli import CLISelectionConfig
from conductor.harness.base import BaseHarness
from conductor.harness.models import HarnessRequest, HarnessResult
from conductor.harness.shell import ShellHarness


@dataclass(slots=True)
class AgentCLIExecution:
    """一次 Agent CLI 执行结果。"""

    cli_name: str
    executable_path: str
    result: HarnessResult


class AgentCLIExecutor:
    """把角色绑定的 Agent CLI 转成非交互执行。"""

    def __init__(
        self,
        cli_selection_config: CLISelectionConfig | None = None,
        shell_harness: BaseHarness | None = None,
    ) -> None:
        self.cli_selection_config = cli_selection_config or CLISelectionConfig()
        self.shell_harness = shell_harness or ShellHarness()
        self._disabled_role_bindings: set[tuple[str, str]] = set()

    def resolve_binding(self, agent: Agent) -> str | None:
        """读取当前 Agent 的有效 CLI 绑定。"""
        selected = set(self.cli_selection_config.selected_cli_names)
        bound_cli = self.cli_selection_config.role_cli_bindings.get(agent.role)
        if not bound_cli:
            return None
        if (agent.role, bound_cli) in self._disabled_role_bindings:
            return None
        if bound_cli not in selected:
            return None
        if not shutil.which(bound_cli):
            return None
        return bound_cli

    def execute(
        self,
        agent: Agent,
        prompt: str,
        execution_mode: str = "documentation",
        timeout_seconds: float = 180.0,
        track_workspace_changes: bool = False,
        workspace_root: str | None = None,
        working_directory: str | None = None,
        stream_callback=None,
    ) -> AgentCLIExecution | None:
        """执行绑定的 Agent CLI；无绑定时返回 None。"""
        cli_name = self.resolve_binding(agent)
        if not cli_name:
            return None
        executable_path = shutil.which(cli_name)
        if not executable_path:
            return None
        request = HarnessRequest(
            command=self._build_command(
                executable_path,
                cli_name,
                prompt,
                execution_mode,
                working_directory=working_directory or str(Path.cwd()),
            ),
            working_directory=working_directory or str(Path.cwd()),
            timeout_seconds=timeout_seconds,
            description=f"{agent.role}:{cli_name}",
            track_workspace_changes=track_workspace_changes,
            workspace_root=workspace_root,
            stream_callback=stream_callback,
            stdin_text=prompt if cli_name == "codex" else None,
        )
        execution = AgentCLIExecution(
            cli_name=cli_name,
            executable_path=executable_path,
            result=self.shell_harness.run(request),
        )
        if self._is_provider_compatibility_failure(execution.result):
            self._disabled_role_bindings.add((agent.role, cli_name))
        return execution

    def is_binding_disabled(self, agent: Agent) -> bool:
        """Return whether a role binding has been disabled in this process."""
        bound_cli = self.cli_selection_config.role_cli_bindings.get(agent.role)
        if not bound_cli:
            return False
        return (agent.role, bound_cli) in self._disabled_role_bindings

    def _is_provider_compatibility_failure(self, result: HarnessResult) -> bool:
        """Detect known provider compatibility failures that should disable a binding."""
        combined_output = f"{result.stdout}\n{result.stderr}"
        return "Unsupported reasoning_effort type" in combined_output

    def _build_command(
        self,
        executable_path: str,
        cli_name: str,
        prompt: str,
        execution_mode: str,
        working_directory: str,
    ) -> list[str]:
        """根据 CLI 名称构建具体命令。"""
        if cli_name == "claude":
            if execution_mode == "code_edit" and os.name == "nt" and executable_path.lower().endswith(".cmd"):
                return self._build_windows_claude_code_edit_command(executable_path, prompt, working_directory)
            command = [executable_path, "-p", prompt, "--output-format", "text", "--effort", "high"]
            if execution_mode == "code_edit":
                command.extend(
                    [
                        "--append-system-prompt",
                        "You are running in a fully trusted local workspace. When the user asks for code changes, you must directly edit files in the workspace instead of describing what to do. Do not claim permission limitations. Make the requested minimal changes and then summarize them.",
                        "--permission-mode",
                        "bypassPermissions",
                        "--dangerously-skip-permissions",
                        "--add-dir",
                        working_directory,
                    ]
                )
            return command
        if cli_name == "codex":
            sandbox_mode = "workspace-write" if execution_mode == "code_edit" else "read-only"
            command = [
                executable_path,
                "exec",
                "-m",
                self.cli_selection_config.codex_model,
                "-c",
                f'model_reasoning_effort="{self.cli_selection_config.codex_reasoning_effort}"',
                "-C",
                working_directory,
                "-s",
                sandbox_mode,
                "--skip-git-repo-check",
            ]
            if execution_mode == "code_edit":
                command.append("--dangerously-bypass-approvals-and-sandbox")
            command.append("-")
            return command
        if cli_name == "qwen":
            return [executable_path, prompt, "-o", "text"]
        if cli_name in {"opencode", "aspirecode"}:
            command = [
                executable_path,
                "run",
                "--dir",
                working_directory,
            ]
            if cli_name == "opencode":
                command.append("--dangerously-skip-permissions")
            if cli_name == "aspirecode":
                command.extend(["--model", self.cli_selection_config.aspirecode_model])
            if execution_mode == "documentation":
                command.extend(["--format", "default"])
            command.append(prompt)
            return command
        raise RuntimeError(f"当前未支持 Agent CLI: {cli_name}")

    def _build_windows_claude_code_edit_command(self, executable_path: str, prompt: str, working_directory: str) -> list[str]:
        """Run Claude code-edit mode through PowerShell on Windows for stable file edits."""
        system_prompt = (
            "You are running in a fully trusted local workspace. "
            "When asked for code changes, you must directly edit files in the workspace instead of describing what to do. "
            "Do not claim permission limitations. Make the requested minimal changes and then summarize them."
        )
        add_dir = working_directory.replace("'", "''")
        exec_path = executable_path.replace("'", "''")
        prompt_here = prompt.replace("'@", "'@`n@'")
        system_prompt_here = system_prompt.replace("'@", "'@`n@'")
        ps_script = (
            f"$prompt = @'\n{prompt_here}\n'@; "
            f"$systemPrompt = @'\n{system_prompt_here}\n'@; "
            f"& '{exec_path}' -p $prompt --output-format text "
            f"--effort high "
            f"--append-system-prompt $systemPrompt "
            f"--permission-mode bypassPermissions "
            f"--dangerously-skip-permissions "
            f"--add-dir '{add_dir}'"
        )
        return ["powershell", "-NoProfile", "-Command", ps_script]
