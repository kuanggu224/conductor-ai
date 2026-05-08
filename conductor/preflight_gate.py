"""Shared helpers for persisted run preflight gate evidence."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path


@dataclass(slots=True)
class PreflightGateSnapshot:
    """Normalized view of a persisted preflight gate payload."""

    recorded: bool = False
    project_root: str = ""
    path: str = ""
    ok: bool | None = None
    errors: list[str] = field(default_factory=list)
    recommendations: list[str] = field(default_factory=list)
    status: str = "not_recorded"
    status_label: str = "未记录"


def preflight_gate_path(project_root: str | Path) -> Path:
    """Return the canonical audit path for one project's run preflight gate."""
    return Path(project_root) / ".conductor" / "diagnostics" / "run-preflight" / "preflight-gate.json"


def write_preflight_gate_payload(project_root: str | Path, payload: dict[str, object]) -> Path:
    """Persist one preflight gate payload and stamp its diagnostics path."""
    path = preflight_gate_path(project_root)
    payload.setdefault("project_root", str(Path(project_root).expanduser().resolve()))
    gate_payload = payload.setdefault("preflight_gate", {})
    if isinstance(gate_payload, dict):
        gate_payload["diagnostics_path"] = str(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def read_preflight_gate(project_root: str | Path | None) -> PreflightGateSnapshot:
    """Read and normalize the persisted preflight gate payload for one project."""
    if not project_root:
        return PreflightGateSnapshot()
    path = preflight_gate_path(project_root)
    resolved_project_root = str(Path(project_root).expanduser().resolve())
    if not path.exists():
        return PreflightGateSnapshot()
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        return PreflightGateSnapshot(
            recorded=True,
            project_root=resolved_project_root,
            path=str(path),
            ok=False,
            errors=[f"Unable to read preflight gate: {error}"],
            status="unreadable",
            status_label="无法读取",
        )
    if not isinstance(payload, dict):
        return PreflightGateSnapshot(
            recorded=True,
            project_root=resolved_project_root,
            path=str(path),
            ok=None,
            errors=["Preflight gate payload is not a JSON object."],
            status="unknown",
            status_label="未知",
        )
    gate_payload = payload.get("preflight_gate", {})
    errors = gate_payload.get("errors", []) if isinstance(gate_payload, dict) else []
    recommendations = gate_payload.get("recommendations", []) if isinstance(gate_payload, dict) else []
    if not isinstance(errors, list):
        errors = [str(errors)]
    if not isinstance(recommendations, list):
        recommendations = [str(recommendations)]
    ok = payload.get("ok")
    normalized_ok = ok if isinstance(ok, bool) else None
    snapshot_project_root = payload.get("project_root")
    if not isinstance(snapshot_project_root, str) or not snapshot_project_root:
        snapshot_project_root = resolved_project_root
    status, status_label = _status_for_ok(normalized_ok)
    return PreflightGateSnapshot(
        recorded=True,
        project_root=snapshot_project_root,
        path=str(path),
        ok=normalized_ok,
        errors=[str(error) for error in errors],
        recommendations=[str(recommendation) for recommendation in recommendations],
        status=status,
        status_label=status_label,
    )


def _status_for_ok(ok: bool | None) -> tuple[str, str]:
    if ok is True:
        return "pass", "通过"
    if ok is False:
        return "fail", "失败"
    return "unknown", "未知"


__all__ = [
    "PreflightGateSnapshot",
    "preflight_gate_path",
    "read_preflight_gate",
    "write_preflight_gate_payload",
]
