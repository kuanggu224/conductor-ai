"""Application entrypoints."""

from __future__ import annotations

from importlib import import_module

__all__ = ["app", "board", "main", "todo_app"]


def __getattr__(name: str):
    if name not in __all__:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    if name == "app":
        module = import_module("app.todo_app")
        value = module.app
        globals()[name] = value
        return value
    module = import_module(f"app.{name}")
    globals()[name] = module
    return module


def __dir__() -> list[str]:
    return sorted({*globals(), *__all__})
