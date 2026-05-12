"""Run preflight readiness summary for real project delivery."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(slots=True)
class ExecutionReadiness:
    """Preflight-level readiness summary for selected execution backends."""

    status: str
    selected_agent_cli: str
    selected_llm_backend: str
    runner_enabled: bool
    blocking_reasons: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    recommendations: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, object]:
        """Return a JSON-safe payload for preflight gate and manifest consumers."""
        return {
            "status": self.status,
            "selected_agent_cli": self.selected_agent_cli,
            "selected_llm_backend": self.selected_llm_backend,
            "runner_enabled": self.runner_enabled,
            "blocking_reasons": list(self.blocking_reasons),
            "warnings": list(self.warnings),
            "recommendations": list(self.recommendations),
        }


READY_LLM_STATUSES = {"ready", "reachable", "models_unavailable"}


def evaluate_execution_readiness(
    *,
    diagnostics,
    errors: list[str],
    recommendations: list[str],
    agent_cli: str | None,
    llm_harness_backend: str | None,
    runner_enabled: bool,
) -> ExecutionReadiness:
    """Build a concise readiness decision from diagnostics and selected run mode."""
    blocking_reasons = list(dict.fromkeys(error for error in errors if error))
    warnings = [
        warning
        for warning in getattr(diagnostics, "warnings", [])
        if warning and warning not in blocking_reasons
    ]
    selected_backend_status = _selected_llm_health_status(diagnostics, llm_harness_backend)
    if llm_harness_backend and selected_backend_status and selected_backend_status not in READY_LLM_STATUSES:
        reason = f"Selected LLM backend `{llm_harness_backend}` health_status={selected_backend_status}."
        if reason not in blocking_reasons:
            blocking_reasons.append(reason)

    if blocking_reasons:
        status = "blocked"
    elif warnings:
        status = "warning"
    else:
        status = "ready"

    return ExecutionReadiness(
        status=status,
        selected_agent_cli=agent_cli or "",
        selected_llm_backend=llm_harness_backend or "",
        runner_enabled=runner_enabled,
        blocking_reasons=blocking_reasons,
        warnings=warnings,
        recommendations=list(dict.fromkeys(item for item in recommendations if item)),
    )


def _selected_llm_health_status(diagnostics, llm_harness_backend: str | None) -> str:
    if not llm_harness_backend:
        return ""
    for backend in getattr(diagnostics, "llm_backends", []):
        if getattr(backend, "backend", "") == llm_harness_backend:
            return str(getattr(backend, "health_status", ""))
    return "missing"


__all__ = ["ExecutionReadiness", "evaluate_execution_readiness"]
