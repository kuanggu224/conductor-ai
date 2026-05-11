"""Console and stdio encoding helpers."""

from __future__ import annotations

import os
import sys
from collections.abc import Mapping


UTF8_CODE_PAGE = 65001

# Broad markers for corrupted UTF-8/GBK text. These are intentionally rare in
# normal project artifacts and are only used as a defensive quality gate.
MOJIBAKE_MARKERS = (
    "\ufffd",
    "\u20ac",
    "\u951b",
    "\u9428",
    "\u93c8",
    "\u6d93",
    "\u7edb",
    "\u7035",
    "\u6d63",
    "\u5a23",
    "\u9366",
    "\u64b3",
    "\u934a",
    "\u7463",
)

# Common Chinese UI labels after UTF-8 bytes are misread as GBK/CP936. A single
# hit can be legitimate Chinese, so looks_like_mojibake requires density.
CP936_MOJIBAKE_MARKERS = (
    "\u95c3",
    "\u5470",
    "\u7ed8",
    "\u7afb",
    "\u9357",
    "\u93b4",
    "\u6220",
    "\u6b91",
    "\u6748",
    "\u64b3",
    "\u53c6",
    "\u6d94",
    "\ufe40",
    "\u6095",
    "\u7035",
    "\u714e",
    "\u56ad",
    "\u5d1f",
    "\u5a23",
    "\u8bf2",
    "\u59de",
    "\u9366",
    "\u74e8",
    "\u934c",
    "\u3124",
    "\u7b09",
    "\u9359",
    "\u9422",
)


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


def looks_like_mojibake(text: str) -> bool:
    """Return whether text appears to contain corrupted UTF-8/GBK mojibake."""
    if not text:
        return False
    if "\ufffd" in text:
        return True
    if any(_is_private_use_character(character) for character in text):
        return True
    marker_hits = sum(text.count(marker) for marker in MOJIBAKE_MARKERS if marker)
    cp936_hits = sum(text.count(marker) for marker in CP936_MOJIBAKE_MARKERS if marker)
    if marker_hits >= 3:
        return True
    if cp936_hits >= 2 and cp936_hits / max(len(text), 1) >= 0.2:
        return True
    return cp936_hits >= 5


def _is_private_use_character(character: str) -> bool:
    codepoint = ord(character)
    return (
        0xE000 <= codepoint <= 0xF8FF
        or 0xF0000 <= codepoint <= 0xFFFFD
        or 0x100000 <= codepoint <= 0x10FFFD
    )


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


__all__ = ["UTF8_CODE_PAGE", "configure_utf8_stdio", "looks_like_mojibake", "utf8_subprocess_environment"]
