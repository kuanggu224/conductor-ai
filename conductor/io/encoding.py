"""Console and stdio encoding helpers."""

from __future__ import annotations

import os
import sys
from collections.abc import Mapping


UTF8_CODE_PAGE = 65001


def configure_utf8_stdio() -> None:
    """Prefer UTF-8 for CLI output on Windows and Unix-like shells.

    This does not change persisted file encodings. It only makes Python entry
    points less dependent on the active Windows console code page.
    """
    os.environ.setdefault("PYTHONIOENCODING", "utf-8")
    os.environ.setdefault("PYTHONUTF8", "1")
    _set_windows_console_code_page()
    _reconfigure_stream(sys.stdin)
    _reconfigure_stream(sys.stdout)
    _reconfigure_stream(sys.stderr)


def utf8_subprocess_environment(overrides: Mapping[str, str] | None = None) -> dict[str, str]:
    """Return an environment that nudges child processes toward UTF-8.

    Conductor often calls Python-based agent wrappers through subprocess. On
    Windows those children can otherwise inherit a legacy ANSI code page and
    emit mojibake even when the parent process writes artifacts as UTF-8.
    """
    environment = os.environ.copy()
    environment.setdefault("PYTHONUTF8", "1")
    environment.setdefault("PYTHONIOENCODING", "utf-8")
    environment.setdefault("LANG", "C.UTF-8")
    environment.setdefault("LC_ALL", "C.UTF-8")
    if overrides:
        environment.update(overrides)
    return environment


def _reconfigure_stream(stream) -> None:
    reconfigure = getattr(stream, "reconfigure", None)
    if reconfigure is None:
        return
    try:
        reconfigure(encoding="utf-8", errors="replace")
    except (OSError, ValueError):
        # Some captured streams used by tests or hosting processes cannot be
        # reconfigured. In that case the caller's stream policy wins.
        return


def _set_windows_console_code_page() -> None:
    if os.name != "nt":
        return
    try:
        import ctypes

        kernel32 = ctypes.windll.kernel32
        kernel32.SetConsoleCP(UTF8_CODE_PAGE)
        kernel32.SetConsoleOutputCP(UTF8_CODE_PAGE)
    except (AttributeError, OSError, ValueError):
        return


__all__ = ["UTF8_CODE_PAGE", "configure_utf8_stdio", "utf8_subprocess_environment"]
