"""Benchmark and evaluator tests."""

import json
from pathlib import Path

from conductor.benchmark import BenchmarkRunResult, BenchmarkRunner, RunEvaluation, default_benchmark_cases, evaluate_run_manifest


def test_default_benchmark_cases_cover_five_scenarios() -> None:
    cases = default_benchmark_cases()

    assert len(cases) == 5
    assert {case.id for case in cases} == {
        "crud_notes",
        "expense_approval",
        "csv_cleaner",
        "rate_limit_service",
        "bug_triage",
    }


def test_benchmark_runner_writes_results(tmp_path) -> None:
    case = default_benchmark_cases()[0]
    runner = BenchmarkRunner(tmp_path)

    result = runner.run_suite(cases=[case], profiles=["mock"], max_steps=1)

    assert result.summary["total"] == 1
    assert result.results[0].case_id == case.id
    assert result.results[0].manifest_path
    assert result.results[0].evaluation.manifest_path == result.results[0].manifest_path
    assert result.json_path.endswith("benchmark-results.json")
    assert result.markdown_path.endswith("benchmark-results.md")
    assert "Req Coverage" in (tmp_path / "benchmark-results.md").read_text(encoding="utf-8")


def test_quality_comparison_runs_single_and_multi_agent_variants(tmp_path, monkeypatch) -> None:
    case = default_benchmark_cases()[0]
    runner = BenchmarkRunner(tmp_path)
    calls: list[bool] = []

    def fake_run_case(case, profile_name, *, use_codex, max_steps, collaboration_enabled=True, variant=""):
        calls.append(collaboration_enabled)
        score = 82 if collaboration_enabled else 70
        manifest_path = tmp_path / f"{variant}.json"
        report_path = tmp_path / f"{variant}.md"
        manifest_path.write_text("{}", encoding="utf-8")
        report_path.write_text("# report", encoding="utf-8")
        evaluation = RunEvaluation(
            project_id=f"project-{variant}",
            case_id=case.id,
            run_profile=profile_name,
            score=score,
            passed=True,
            checks={"completed": True},
            metrics={},
            findings=[],
            manifest_path=str(manifest_path),
            report_path=str(report_path),
        )
        return BenchmarkRunResult(
            case_id=case.id,
            case_name=case.name,
            run_profile=profile_name,
            project_root=str(tmp_path / variant),
            manifest_path=str(manifest_path),
            report_path=str(report_path),
            duration_ms=1,
            evaluation=evaluation,
        )

    monkeypatch.setattr(runner, "run_case", fake_run_case)

    result = runner.run_quality_comparison(cases=[case], profile_name="api_mock", max_steps=1)

    assert calls == [False, True]
    assert result.results[0].score_delta == 12
    assert result.results[0].winner == "multi_agent"
    assert result.summary["multi_agent_wins"] == 1
    assert result.json_path.endswith("quality-comparison-results.json")
    assert "Single Score" in Path(result.markdown_path).read_text(encoding="utf-8")


def test_evaluator_scores_manifest_and_flags_mock(tmp_path) -> None:
    case = default_benchmark_cases()[0]
    runner = BenchmarkRunner(tmp_path)
    result = runner.run_suite(cases=[case], profiles=["mock"], max_steps=1)

    evaluation = evaluate_run_manifest(result.results[0].manifest_path, case)

    assert evaluation.project_id
    assert evaluation.metrics["artifact_count"] >= 1
    assert evaluation.checks["has_manifest"] is True
    assert evaluation.checks["has_report"] is True
    assert any("mock" in finding.lower() for finding in evaluation.findings)


def test_evaluator_detects_cli_run_from_manifest(tmp_path) -> None:
    manifest = {
        "project_id": "project-1",
        "run_profile": "design_cli_only",
        "project_root": str(tmp_path),
        "final_status": "completed",
        "summary": {"blocked_count": 0},
        "artifacts": [
            {
                "id": "artifact-1",
                "kind": "design_overview",
                "source_backend": "agent_cli/codex",
                "path": str(tmp_path / "artifact.md"),
            }
        ],
        "executions": [
            {
                "workitem_id": "workitem-1",
                "agent_id": "agent-designer",
                "status": "success",
                "source_backend": "agent_cli/codex",
            }
        ],
        "cli_runs": [
            {
                "workitem_id": "workitem-1",
                "agent_id": "agent-designer",
                "status": "success",
                "source_backend": "agent_cli/codex",
            }
        ],
        "workitems": [{"id": "workitem-1", "failure_type": ""}],
        "files": {
            "log": str(tmp_path / "run.jsonl"),
            "report": str(tmp_path / "report.md"),
        },
    }
    (tmp_path / "run.jsonl").write_text("{}", encoding="utf-8")
    (tmp_path / "report.md").write_text("# report", encoding="utf-8")
    manifest_path = tmp_path / "manifest.json"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    evaluation = evaluate_run_manifest(manifest_path, require_cli=True)

    assert evaluation.checks["has_cli_run"] is True
    assert evaluation.checks["no_mock_artifacts"] is True
    assert evaluation.score >= 70


def test_evaluator_flags_missing_requirement_coverage_from_manifest(tmp_path) -> None:
    manifest = {
        "project_id": "project-coverage",
        "run_profile": "api_mock",
        "project_root": str(tmp_path),
        "final_status": "completed",
        "summary": {"blocked_count": 0, "requirement_coverage_status": "missing_coverage"},
        "artifacts": [
            {
                "id": "artifact-1",
                "kind": "acceptance_check",
                "source_backend": "cli/api_mock",
                "path": str(tmp_path / "artifact.md"),
            }
        ],
        "executions": [
            {
                "workitem_id": "workitem-validation",
                "agent_id": "agent-tester",
                "status": "success",
                "source_backend": "cli/api_mock",
            }
        ],
        "cli_runs": [
            {
                "workitem_id": "workitem-validation",
                "agent_id": "agent-tester",
                "status": "success",
                "source_backend": "cli/api_mock",
            }
        ],
        "workitems": [{"id": "workitem-validation", "failure_type": ""}],
        "requirement_coverage_results": [
            {
                "workitem_id": "workitem-validation",
                "passed": False,
                "status": "missing_coverage",
                "missing_labels": ["refresh persistence"],
            }
        ],
        "files": {
            "log": str(tmp_path / "run.jsonl"),
            "report": str(tmp_path / "report.md"),
        },
    }
    (tmp_path / "run.jsonl").write_text("{}", encoding="utf-8")
    (tmp_path / "report.md").write_text("# report", encoding="utf-8")
    manifest_path = tmp_path / "manifest.json"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    evaluation = evaluate_run_manifest(manifest_path, require_cli=True)

    assert evaluation.checks["requirement_coverage"] is False
    assert evaluation.metrics["requirement_coverage_results"] == 1
    assert evaluation.metrics["missing_requirement_coverage"] == 1
    assert any("refresh persistence" in finding for finding in evaluation.findings)
