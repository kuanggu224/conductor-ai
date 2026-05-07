"""Top-level Conductor package exports.

Imports are resolved lazily to avoid circular import chains during package
initialization.
"""

from __future__ import annotations

from importlib import import_module

_EXPORTS = {
    "Agent": ("conductor.agents.agent", "Agent"),
    "CLISelectionConfig": ("conductor.config.cli", "CLISelectionConfig"),
    "Artifact": ("conductor.domain.models", "Artifact"),
    "Capability": ("conductor.domain.models", "Capability"),
    "ContextPack": ("conductor.context.models", "ContextPack"),
    "ExecutionScopeConfig": ("conductor.config.execution", "ExecutionScopeConfig"),
    "Execution": ("conductor.domain.models", "Execution"),
    "ExecutionResult": ("conductor.domain.models", "ExecutionResult"),
    "ExecutionStatus": ("conductor.domain.models", "ExecutionStatus"),
    "GateDecision": ("conductor.workflow.template", "GateDecision"),
    "GlobalMemory": ("conductor.memory.models", "GlobalMemory"),
    "LLMUsagePolicy": ("conductor.config.llm", "LLMUsagePolicy"),
    "Project": ("conductor.domain.models", "Project"),
    "ProjectStatus": ("conductor.domain.models", "ProjectStatus"),
    "RouteDecision": ("conductor.domain.models", "RouteDecision"),
    "SharedProjectState": ("conductor.domain.models", "SharedProjectState"),
    "SystemConfig": ("conductor.config.system", "SystemConfig"),
    "Stage": ("conductor.domain.models", "Stage"),
    "TodoItem": ("conductor.todo.models", "TodoItem"),
    "TODO_SESSION_COOKIE": ("conductor.todo.service", "TODO_SESSION_COOKIE"),
    "create_todo_service": ("conductor.todo.service", "create_todo_service"),
    "TodoListStatus": ("conductor.todo.service", "TodoListStatus"),
    "ensure_todo_registry": ("conductor.todo.service", "ensure_todo_registry"),
    "reset_todo_registry": ("conductor.todo.service", "reset_todo_registry"),
    "resolve_todo_service": ("conductor.todo.service", "resolve_todo_service"),
    "TodoService": ("conductor.todo.service", "TodoService"),
    "WorkItem": ("conductor.domain.models", "WorkItem"),
    "WorkItemStatus": ("conductor.domain.models", "WorkItemStatus"),
    "WorkflowTemplate": ("conductor.workflow.template", "WorkflowTemplate"),
}

__all__ = list(_EXPORTS)


def __getattr__(name: str):
    if name not in _EXPORTS:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    module_name, attr_name = _EXPORTS[name]
    value = getattr(import_module(module_name), attr_name)
    globals()[name] = value
    return value


def __dir__() -> list[str]:
    return sorted({*globals(), *_EXPORTS})
