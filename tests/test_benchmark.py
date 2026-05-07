"""Benchmark and evaluator tests."""

import json

from conductor.benchmark import BenchmarkRunner, default_benchmark_cases, evaluate_run_manifest


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
