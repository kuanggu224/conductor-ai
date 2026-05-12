"""Execution readiness tests."""

from types import SimpleNamespace

from conductor.execution_readiness import evaluate_execution_readiness


def test_execution_readiness_reports_ready_when_selected_backend_is_ready() -> None:
    diagnostics = SimpleNamespace(
        warnings=[],
        llm_backends=[SimpleNamespace(backend="cloud", health_status="ready")],
    )

    readiness = evaluate_execution_readiness(
        diagnostics=diagnostics,
        errors=[],
        recommendations=[],
        agent_cli=None,
        llm_harness_backend="cloud",
        runner_enabled=False,
    )

    assert readiness.status == "ready"
    assert readiness.selected_llm_backend == "cloud"
    assert readiness.blocking_reasons == []


def test_execution_readiness_blocks_failed_selected_backend() -> None:
    diagnostics = SimpleNamespace(
        warnings=[],
        llm_backends=[SimpleNamespace(backend="cloud", health_status="failed")],
    )

    readiness = evaluate_execution_readiness(
        diagnostics=diagnostics,
        errors=[],
        recommendations=["check cloud"],
        agent_cli=None,
        llm_harness_backend="cloud",
        runner_enabled=False,
    )

    assert readiness.status == "blocked"
    assert readiness.blocking_reasons == ["Selected LLM backend `cloud` health_status=failed."]
    assert readiness.recommendations == ["check cloud"]


def test_execution_readiness_keeps_non_blocking_warnings() -> None:
    diagnostics = SimpleNamespace(
        warnings=["codex version probe skipped"],
        llm_backends=[],
    )

    readiness = evaluate_execution_readiness(
        diagnostics=diagnostics,
        errors=[],
        recommendations=[],
        agent_cli="codex",
        llm_harness_backend=None,
        runner_enabled=False,
    )

    assert readiness.status == "warning"
    assert readiness.warnings == ["codex version probe skipped"]
