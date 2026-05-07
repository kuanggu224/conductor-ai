"""Domain model exports resolved lazily."""

from __future__ import annotations

from importlib import import_module

_EXPORTS = {
    "AgentCapabilityStats": ("conductor.domain.models", "AgentCapabilityStats"),
    "AgentActivation": ("conductor.domain.models", "AgentActivation"),
    "Artifact": ("conductor.domain.models", "Artifact"),
    "Capability": ("conductor.domain.models", "Capability"),
    "Execution": ("conductor.domain.models", "Execution"),
    "ExecutionResult": ("conductor.domain.models", "ExecutionResult"),
    "ExecutionStatus": ("conductor.domain.models", "ExecutionStatus"),
    "Project": ("conductor.domain.models", "Project"),
    "ProjectStatus": ("conductor.domain.models", "ProjectStatus"),
    "RouteDecision": ("conductor.domain.models", "RouteDecision"),
    "SharedProjectState": ("conductor.domain.models", "SharedProjectState"),
    "Stage": ("conductor.domain.models", "Stage"),
    "TaskAssignment": ("conductor.domain.models", "TaskAssignment"),
    "TaskAssignmentStatus": ("conductor.domain.models", "TaskAssignmentStatus"),
    "WorkItem": ("conductor.domain.models", "WorkItem"),
    "WorkItemStatus": ("conductor.domain.models", "WorkItemStatus"),
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
