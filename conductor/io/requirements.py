"""Requirement input helpers.

Shell pipelines are not a reliable Unicode boundary on every platform. Keep
natural-language requirements on UTF-8 files or HTTP JSON whenever possible.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any


class RequirementInputError(ValueError):
    """Raised when a requirement input cannot be resolved."""


def load_requirement_text(
    requirement: str | None = None,
    requirement_file: str | Path | None = None,
    requirement_json_file: str | Path | None = None,
    json_key: str = "requirement",
) -> str:
    """Resolve requirement text from direct text, UTF-8 text file, or JSON file.

    Priority is explicit file input first, then direct text. This lets callers
    avoid shell encoding loss on Windows PowerShell without branching by OS.
    """
    if requirement_json_file:
        text = _load_requirement_from_json(requirement_json_file, json_key=json_key)
    elif requirement_file:
        text = Path(requirement_file).expanduser().read_text(encoding="utf-8-sig")
    else:
        text = requirement or ""
    text = text.strip()
    if not text:
        raise RequirementInputError("Requirement is empty")
    return text


def _load_requirement_from_json(path: str | Path, json_key: str) -> str:
    payload = json.loads(Path(path).expanduser().read_text(encoding="utf-8-sig"))
    value = _read_json_path(payload, json_key)
    if not isinstance(value, str):
        raise RequirementInputError(f"JSON key `{json_key}` must contain a string requirement")
    return value


def _read_json_path(payload: Any, json_key: str) -> Any:
    current = payload
    for part in json_key.split("."):
        if not isinstance(current, dict) or part not in current:
            raise RequirementInputError(f"JSON key `{json_key}` not found")
        current = current[part]
    return current
