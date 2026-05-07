"""Benchmark cases, runner, and run evaluator."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path
from time import perf_counter
from typing import Any

from conductor.config.cli import CLISelectionConfig
from conductor.config.execution import RunProfile, resolve_run_profile
from conductor.controller.engine import ConductorEngine
from conductor.state.file_store import FileStateStore


@dataclass(slots=True)
class BenchmarkCase:
    """One fixed benchmark requirement."""

    id: str
    name: str
    requirement: str
    expected_keywords: list[str] = field(default_factory=list)
    tags: list[str] = field(default_factory=list)


@dataclass(slots=True)
class RunEvaluation:
    """Structured score for one Conductor run."""

    project_id: str
    case_id: str
    run_profile: str
    score: int
    passed: bool
    checks: dict[str, bool]
    metrics: dict[str, int | str]
    findings: list[str]
    manifest_path: str
    report_path: str


@dataclass(slots=True)
class BenchmarkRunResult:
    """One benchmark case/profile run result."""

    case_id: str
    case_name: str
    run_profile: str
    project_root: str
    manifest_path: str
    report_path: str
    duration_ms: int
    evaluation: RunEvaluation


@dataclass(slots=True)
class BenchmarkSuiteResult:
    """A complete benchmark suite output."""

    output_dir: str
    results: list[BenchmarkRunResult]
    summary: dict[str, int | float]
    json_path: str
    markdown_path: str


def default_benchmark_cases() -> list[BenchmarkCase]:
    """Return the fixed benchmark suite used for platform regression."""
    return [
        BenchmarkCase(
            id="crud_notes",
            name="Notes CRUD",
            requirement=(
                "Implement a tiny notes app with title/content CRUD, REST API, simple HTML UI, "
                "basic validation, empty state, and tests for create/list/update/delete."
            ),
            expected_keywords=["notes", "title", "content", "create", "delete"],
            tags=["crud", "api", "ui", "tests"],
        ),
        BenchmarkCase(
            id="expense_approval",
            name="Expense Approval",
            requirement=(
                "Build a small expense approval workflow: employees submit expenses, managers approve "
                "or reject, status is visible in the UI, and API responses include validation errors."
            ),
            expected_keywords=["expense", "approve", "reject", "status"],
            tags=["workflow", "api", "ui"],
        ),
        BenchmarkCase(
            id="csv_cleaner",
            name="CSV Cleaner",
            requirement=(
                "Create a local CSV cleaner service that uploads or reads a CSV, trims whitespace, "
                "removes duplicate rows, reports invalid rows, and exports a cleaned file."
            ),
            expected_keywords=["csv", "duplicate", "invalid", "export"],
            tags=["file", "data"],
        ),
        BenchmarkCase(
            id="rate_limit_service",
            name="Rate Limit Service",
            requirement=(
                "Implement a simple API rate limit service with per-client counters, reset window, "
                "429 responses, and tests covering allowed and blocked requests."
            ),
            expected_keywords=["rate", "limit", "429", "client"],
            tags=["api", "tests"],
        ),
        BenchmarkCase(
            id="bug_triage",
            name="Bug Triage Board",
            requirement=(
                "Build a bug triage board where users can create bugs with severity, reproduction steps, "
                "assignee, status transitions, filters, and a clear acceptance checklist."
            ),
            expected_keywords=["bug", "severity", "status", "filter"],
            tags=["ambiguous", "ui", "workflow"],
        ),
    ]


def evaluate_run_manifest(
    manifest_path: str | Path,
    case: BenchmarkCase | None = None,
    *,
    require_cli: bool = False,
    require_code: bool = False,
) -> RunEvaluation:
    """Evaluate one run manifest and return a 0-100 score."""
    path = Path(manifest_path)
    manifest: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
    project_root = Path(str(manifest.get("project_root", "")))
    artifacts = list(manifest.get("artifacts", []))
    executions = list(manifest.get("executions", []))
    cli_runs = list(manifest.get("cli_runs", []))
    workitems = list(manifest.get("workitems", []))
    files = dict(manifest.get("files", {}))
    final_status = str(manifest.get("final_status", manifest.get("status", "")))

    has_mock_artifact = any(str(item.get("source_backend", "")).startswith("mock") for item in artifacts)
    has_cli_run = bool(cli_runs)
    has_success_execution = any(item.get("status") == "success" for item in executions)
    has_blockers = int(manifest.get("summary", {}).get("blocked_count", 0)) > 0
    has_report = Path(str(files.get("report", ""))).exists()
    has_log = Path(str(files.get("log", ""))).exists()
    has_manifest = path.exists()
    code_files = _find_code_files(project_root)
    has_code_files = bool(code_files)
    tests_passed = any(
        item.get("status") == "success" and str(item.get("source_backend", "")).startswith("cli/")
        for item in executions
    )
    retry_or_failure = any(item.get("failure_type") for item in workitems)
    keyword_coverage = _keyword_coverage(case.expected_keywords if case else [], artifacts)

    checks = {
        "completed": final_status == "completed",
        "has_cli_run": has_cli_run,
        "no_mock_artifacts": not has_mock_artifact,
        "has_success_execution": has_success_execution,
        "has_report": has_report,
        "has_log": has_log,
        "has_manifest": has_manifest,
        "has_code_files": has_code_files,
        "tests_passed": tests_passed,
        "no_blockers": not has_blockers,
        "keyword_coverage": keyword_coverage >= 60 if case and case.expected_keywords else True,
    }
    score = 0
    score += 15 if checks["completed"] else 0
    score += 15 if checks["has_success_execution"] else 0
    score += 15 if checks["has_cli_run"] or not require_cli else 0
    score += 15 if checks["no_mock_artifacts"] else 0
    score += 10 if checks["has_report"] and checks["has_log"] and checks["has_manifest"] else 0
    score += 10 if checks["has_code_files"] or not require_code else 0
    score += 10 if checks["tests_passed"] or not require_code else 0
    score += 10 if checks["keyword_coverage"] else 0
    score = min(score, 100)

    findings: list[str] = []
    if has_mock_artifact:
        findings.append("Run contains mock or mock_fallback artifacts.")
    if require_cli and not has_cli_run:
        findings.append("CLI run was required but no cli_runs were recorded.")
    if require_code and not has_code_files:
        findings.append("Code output was required but no source files were found outside .conductor.")
    if require_code and not tests_passed:
        findings.append("Code output was required but no successful test/harness execution was recorded.")
    if has_blockers:
        findings.append("Run ended with blockers.")
    if retry_or_failure:
        findings.append("Some WorkItems recorded failure or retry metadata.")

    return RunEvaluation(
        project_id=str(manifest.get("project_id", "")),
        case_id=case.id if case else "",
        run_profile=str(manifest.get("run_profile", "")),
        score=score,
        passed=score >= 70 and not has_blockers,
        checks=checks,
        metrics={
            "artifact_count": len(artifacts),
            "execution_count": len(executions),
            "cli_runs": len(cli_runs),
            "code_files": len(code_files),
            "keyword_coverage": keyword_coverage,
        },
        findings=findings,
        manifest_path=str(path),
        report_path=str(files.get("report", "")),
    )


class BenchmarkRunner:
    """Run fixed benchmark cases through Conductor and evaluate outputs."""

    def __init__(self, output_dir: str | Path) -> None:
        self.output_dir = Path(output_dir).expanduser().resolve()
        self.output_dir.mkdir(parents=True, exist_ok=True)

    def run_suite(
        self,
        cases: list[BenchmarkCase] | None = None,
        profiles: list[str] | None = None,
        *,
        use_codex: bool = False,
        max_steps: int = 80,
    ) -> BenchmarkSuiteResult:
        """Run selected cases/profiles and write suite reports."""
        selected_cases = cases or default_benchmark_cases()
        selected_profiles = profiles or [RunProfile.MOCK.value]
        results: list[BenchmarkRunResult] = []
        for case in selected_cases:
            for profile_name in selected_profiles:
                results.append(
                    self.run_case(
                        case=case,
                        profile_name=profile_name,
                        use_codex=use_codex,
                        max_steps=max_steps,
                    )
                )
        json_path = self.output_dir / "benchmark-results.json"
        markdown_path = self.output_dir / "benchmark-results.md"
        summary = self._summary(results)
        json_path.write_text(
            json.dumps(
                {
                    "summary": summary,
                    "results": [asdict(result) for result in results],
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
        markdown_path.write_text(self._render_markdown(results, summary), encoding="utf-8")
        return BenchmarkSuiteResult(
            output_dir=str(self.output_dir),
            results=results,
            summary=summary,
            json_path=str(json_path),
            markdown_path=str(markdown_path),
        )

    def run_case(
        self,
        case: BenchmarkCase,
        profile_name: str,
        *,
        use_codex: bool,
        max_steps: int,
    ) -> BenchmarkRunResult:
        """Run one benchmark case/profile."""
        run_profile = resolve_run_profile(profile_name)
        project_root = self.output_dir / f"{case.id}__{run_profile.profile.value}"
        project_root.mkdir(parents=True, exist_ok=True)
        cli_config = _build_cli_config(use_codex, run_profile)
        started = perf_counter()
        engine = ConductorEngine(
            log_dir=project_root / ".conductor" / "logs",
            artifact_dir=project_root / ".conductor" / "artifacts",
            state_store=FileStateStore(project_root / ".conductor" / "state"),
            cli_selection_config=cli_config,
            run_profile=run_profile.profile,
            require_real_design_outputs=run_profile.require_real_design_outputs,
            require_real_code_outputs=run_profile.require_real_code_outputs,
        )
        state = engine.create_project(case.requirement, project_root=str(project_root))
        state = engine.run_project(state.project.id, max_steps=max_steps)
        report_path = engine.write_project_report(state.project.id)
        manifest_path = engine.write_run_manifest(state.project.id, report_path)
        duration_ms = int((perf_counter() - started) * 1000)
        evaluation = evaluate_run_manifest(
            manifest_path,
            case,
            require_cli=bool(run_profile.cli_roles),
            require_code=run_profile.require_real_code_outputs,
        )
        return BenchmarkRunResult(
            case_id=case.id,
            case_name=case.name,
            run_profile=run_profile.profile.value,
            project_root=str(project_root),
            manifest_path=str(manifest_path),
            report_path=str(report_path),
            duration_ms=duration_ms,
            evaluation=evaluation,
        )

    def _summary(self, results: list[BenchmarkRunResult]) -> dict[str, int | float]:
        if not results:
            return {"total": 0, "passed": 0, "average_score": 0.0}
        return {
            "total": len(results),
            "passed": sum(1 for result in results if result.evaluation.passed),
            "average_score": round(sum(result.evaluation.score for result in results) / len(results), 2),
        }

    def _render_markdown(self, results: list[BenchmarkRunResult], summary: dict[str, int | float]) -> str:
        lines = [
            "# Conductor Benchmark Results",
            "",
            f"- Total: {summary['total']}",
            f"- Passed: {summary['passed']}",
            f"- Average Score: {summary['average_score']}",
            "",
            "| Case | Profile | Score | Passed | CLI Runs | Mock Free | Completed | Manifest |",
            "|---|---|---:|---|---:|---|---|---|",
        ]
        for result in results:
            checks = result.evaluation.checks
            lines.append(
                "| "
                f"{result.case_id} | {result.run_profile} | {result.evaluation.score} | "
                f"{'yes' if result.evaluation.passed else 'no'} | "
                f"{result.evaluation.metrics['cli_runs']} | "
                f"{'yes' if checks['no_mock_artifacts'] else 'no'} | "
                f"{'yes' if checks['completed'] else 'no'} | "
                f"{result.manifest_path} |"
            )
        lines.append("")
        return "\n".join(lines)


def _build_cli_config(use_codex: bool, run_profile) -> CLISelectionConfig:
    roles = ["designer", "backend_engineer", "frontend_engineer", "tester"]
    selected_cli_names = ["codex"] if use_codex and run_profile.cli_roles else []
    return CLISelectionConfig(
        selected_cli_names=selected_cli_names,
        role_cli_bindings={
            role: "codex" if use_codex and run_profile.uses_cli_for_role(role) else None
            for role in roles
        },
        codex_model="gpt-5.4-mini",
        codex_reasoning_effort="medium",
    )


def _find_code_files(project_root: Path) -> list[Path]:
    if not project_root.exists():
        return []
    suffixes = {".py", ".js", ".ts", ".tsx", ".jsx", ".html", ".css", ".json"}
    ignored_parts = {".conductor", "__pycache__", ".git", "node_modules"}
    files: list[Path] = []
    for path in project_root.rglob("*"):
        if not path.is_file() or path.suffix.lower() not in suffixes:
            continue
        if any(part in ignored_parts for part in path.parts):
            continue
        files.append(path)
    return files


def _keyword_coverage(expected_keywords: list[str], artifacts: list[dict[str, Any]]) -> int:
    if not expected_keywords:
        return 100
    text = " ".join(str(item.get("id", "")) + " " + str(item.get("kind", "")) + " " + str(item.get("path", "")) for item in artifacts).lower()
    matched = sum(1 for keyword in expected_keywords if keyword.lower() in text)
    return int((matched / len(expected_keywords)) * 100)


__all__ = [
    "BenchmarkCase",
    "BenchmarkRunResult",
    "BenchmarkRunner",
    "BenchmarkSuiteResult",
    "RunEvaluation",
    "default_benchmark_cases",
    "evaluate_run_manifest",
]
