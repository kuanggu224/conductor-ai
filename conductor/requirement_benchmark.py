"""Requirement-stage quality evaluation and platform-vs-direct comparison."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from conductor.agents.llm import LLMHTTPConfig
from conductor.harness.llm import LLMHarnessRequest, OpenAICompatibleLLMHarness


@dataclass(slots=True)
class RequirementBenchmarkCase:
    """One requirement-stage evaluation case."""

    id: str
    name: str
    requirement: str
    expected_keywords: list[str] = field(default_factory=list)
    required_aspects: list[str] = field(default_factory=list)


@dataclass(slots=True)
class RequirementEvaluation:
    """Score for one requirement document."""

    case_id: str
    score: int
    passed: bool
    checks: dict[str, bool]
    metrics: dict[str, int]
    findings: list[str]


@dataclass(slots=True)
class RequirementComparison:
    """Comparison between a Conductor-produced requirement artifact and a direct model output."""

    case_id: str
    platform: RequirementEvaluation
    direct: RequirementEvaluation
    delta: int
    winner: str
    passed: bool
    min_score: int
    min_delta: int


@dataclass(slots=True)
class RequirementComparisonSuiteResult:
    """Persistable suite result for requirement-stage comparisons."""

    output_dir: str
    comparisons: list[RequirementComparison]
    summary: dict[str, int | float]
    json_path: str
    markdown_path: str


@dataclass(slots=True)
class DirectRequirementBaselineResult:
    """Output from a direct model baseline that bypasses Conductor orchestration."""

    case_id: str
    content: str
    output_path: str
    model: str
    duration_ms: int


@dataclass(slots=True)
class RequirementLLMPreflightResult:
    """Connectivity check result for a requirement benchmark LLM backend."""

    backend: str
    success: bool
    model: str
    base_url: str
    duration_ms: int
    error: str = ""
    content: str = ""


STOPWORDS = {
    "一个",
    "支持",
    "包含",
    "以及",
    "并且",
    "需要",
    "the",
    "and",
    "with",
    "that",
    "where",
    "small",
    "simple",
    "create",
    "build",
    "implement",
}


SECTION_TERMS: dict[str, tuple[str, ...]] = {
    "goal": ("目标", "用户目标", "业务目标", "goal", "objective", "user goal"),
    "scope": ("范围", "边界", "非目标", "不包含", "scope", "out of scope", "non-goal"),
    "acceptance": ("验收", "通过标准", "验收标准", "acceptance", "expected output", "given", "when", "then"),
    "risk": ("风险", "歧义", "假设", "待确认", "问题", "risk", "ambiguity", "assumption", "question"),
    "testability": ("测试", "验证", "可测", "test", "verify", "validation"),
    "implementation": ("接口", "数据", "页面", "交互", "持久化", "api", "data", "ui", "interaction", "storage"),
    "non_goal": ("非目标", "不做", "不包含", "out of scope", "non-goal", "non goal"),
    "open_question": ("待确认", "待澄清", "开放问题", "假设", "assumption", "open question", "to confirm"),
    "edge_case": ("边界", "异常", "错误", "空状态", "空列表", "edge", "boundary", "exception", "error", "empty state"),
    "downstream": ("下游", "后续", "交付约束", "需求基线", "downstream", "handoff", "contract", "constraint"),
}


ASPECT_TERMS: dict[str, tuple[str, ...]] = {
    "ui": ("ui", "页面", "界面", "交互", "表单", "列表"),
    "data": ("data", "数据", "字段", "状态", "localstorage", "存储"),
    "export": ("export", "导出", "下载", "csv"),
    "persistence": ("persistence", "持久化", "保留数据", "刷新后保留", "localstorage", "存储"),
    "filtering": ("filtering", "筛选", "过滤", "filter"),
    "workflow": ("workflow", "流程", "流转", "提交", "审批"),
    "roles": ("roles", "角色", "员工", "经理", "manager", "employee"),
    "api": ("api", "接口", "http", "请求", "响应"),
    "validation": ("validation", "校验", "验证", "错误", "invalid", "error"),
    "status": ("status", "状态", "进度"),
    "file_io": ("file", "文件", "读取", "上传", "导入", "csv"),
    "data_quality": ("data quality", "清洗", "去重", "重复", "无效", "trim", "duplicate", "invalid"),
}


def default_requirement_benchmark_cases() -> list[RequirementBenchmarkCase]:
    """Return fixed cases for requirement-stage regression."""
    return [
        RequirementBenchmarkCase(
            id="reading_list",
            name="Reading List Web App",
            requirement=(
                "个人读书清单 Web 应用：添加书名、作者、阅读状态、评分、备注；"
                "按状态筛选；导出 CSV；刷新后保留数据。"
            ),
            expected_keywords=["书名", "作者", "状态", "评分", "CSV", "保留数据"],
            required_aspects=["ui", "data", "export", "persistence", "filtering"],
        ),
        RequirementBenchmarkCase(
            id="expense_approval",
            name="Expense Approval Workflow",
            requirement=(
                "Build a small expense approval workflow where employees submit expenses, "
                "managers approve or reject them, status is visible, and invalid inputs return clear errors."
            ),
            expected_keywords=["expense", "submit", "approve", "reject", "status", "error"],
            required_aspects=["workflow", "roles", "api", "validation", "status"],
        ),
        RequirementBenchmarkCase(
            id="csv_cleaner",
            name="CSV Cleaner",
            requirement=(
                "Create a local CSV cleaner that reads a CSV, trims whitespace, removes duplicate rows, "
                "reports invalid rows, and exports a cleaned file."
            ),
            expected_keywords=["CSV", "trim", "duplicate", "invalid", "export"],
            required_aspects=["file_io", "data_quality", "export", "validation"],
        ),
    ]


def build_requirement_case_from_text(
    case_id: str,
    requirement: str,
    *,
    name: str | None = None,
) -> RequirementBenchmarkCase:
    """Build an evaluation case from an arbitrary project requirement."""
    return RequirementBenchmarkCase(
        id=case_id,
        name=name or case_id,
        requirement=requirement,
        expected_keywords=_extract_requirement_keywords(requirement),
        required_aspects=_infer_required_aspects(requirement),
    )


def evaluate_requirement_document(
    document: str,
    case: RequirementBenchmarkCase,
    *,
    min_score: int = 70,
) -> RequirementEvaluation:
    """Evaluate whether a requirement document is complete enough to guide downstream agents."""
    text = document.lower()
    keyword_coverage = _coverage(case.expected_keywords, text)
    aspect_coverage = _aspect_coverage(case.required_aspects, text)
    checks = {
        "keyword_coverage": keyword_coverage >= 70,
        "aspect_coverage": aspect_coverage >= 60 if case.required_aspects else True,
        "has_goal": _contains_any(text, SECTION_TERMS["goal"]),
        "has_scope_boundary": _contains_any(text, SECTION_TERMS["scope"]),
        "has_acceptance_criteria": _contains_any(text, SECTION_TERMS["acceptance"]),
        "has_risk_or_questions": _contains_any(text, SECTION_TERMS["risk"]),
        "has_testability": _contains_any(text, SECTION_TERMS["testability"]),
        "has_implementation_boundary": _contains_any(text, SECTION_TERMS["implementation"]),
        "has_non_goals": _contains_any(text, SECTION_TERMS["non_goal"]),
        "has_open_questions_or_assumptions": _contains_any(text, SECTION_TERMS["open_question"]),
        "has_edge_cases": _contains_any(text, SECTION_TERMS["edge_case"]),
        "has_downstream_constraints": _contains_any(text, SECTION_TERMS["downstream"]),
        "not_mock_or_placeholder": not _is_mock_or_placeholder_document(text),
    }
    score = 0
    score += 15 if checks["keyword_coverage"] else int(keyword_coverage * 0.15)
    score += 10 if checks["aspect_coverage"] else int(aspect_coverage * 0.1)
    score += 8 if checks["has_goal"] else 0
    score += 12 if checks["has_scope_boundary"] else 0
    score += 17 if checks["has_acceptance_criteria"] else 0
    score += 8 if checks["has_risk_or_questions"] else 0
    score += 8 if checks["has_testability"] else 0
    score += 5 if checks["has_implementation_boundary"] else 0
    score += 7 if checks["has_non_goals"] else 0
    score += 4 if checks["has_open_questions_or_assumptions"] else 0
    score += 4 if checks["has_edge_cases"] else 0
    score += 2 if checks["has_downstream_constraints"] else 0
    score = min(score, 100)
    if not checks["not_mock_or_placeholder"]:
        score = min(score, 35)
    findings: list[str] = []
    if not checks["not_mock_or_placeholder"]:
        findings.append("Document appears to be a mock, fallback, or platform placeholder output.")
    if not checks["keyword_coverage"]:
        findings.append("Requirement keywords are not sufficiently covered.")
    if not checks["aspect_coverage"]:
        findings.append("Required aspects are not sufficiently covered.")
    if not checks["has_scope_boundary"]:
        findings.append("Scope boundaries or non-goals are missing.")
    if not checks["has_acceptance_criteria"]:
        findings.append("Concrete acceptance criteria are missing.")
    if not checks["has_risk_or_questions"]:
        findings.append("Risks, assumptions, ambiguities, or open questions are missing.")
    if not checks["has_testability"]:
        findings.append("Testability or validation guidance is missing.")
    if not checks["has_non_goals"]:
        findings.append("Explicit non-goals or out-of-scope boundaries are missing.")
    if not checks["has_open_questions_or_assumptions"]:
        findings.append("Open questions or assumptions are missing.")
    if not checks["has_edge_cases"]:
        findings.append("Boundary, error, empty-state, or edge-case behavior is missing.")
    if not checks["has_downstream_constraints"]:
        findings.append("Downstream handoff constraints are missing.")
    return RequirementEvaluation(
        case_id=case.id,
        score=score,
        passed=score >= min_score,
        checks=checks,
        metrics={
            "keyword_coverage": keyword_coverage,
            "aspect_coverage": aspect_coverage,
            "document_chars": len(document),
        },
        findings=findings,
    )


def compare_requirement_documents(
    *,
    case: RequirementBenchmarkCase,
    platform_document: str,
    direct_document: str,
    min_score: int = 70,
    min_delta: int = 5,
) -> RequirementComparison:
    """Compare platform-assisted requirement output against direct model output."""
    platform = evaluate_requirement_document(platform_document, case, min_score=min_score)
    direct = evaluate_requirement_document(direct_document, case, min_score=min_score)
    delta = platform.score - direct.score
    if delta > 0:
        winner = "platform"
    elif delta < 0:
        winner = "direct"
    else:
        winner = "tie"
    return RequirementComparison(
        case_id=case.id,
        platform=platform,
        direct=direct,
        delta=delta,
        winner=winner,
        passed=platform.passed and delta >= min_delta,
        min_score=min_score,
        min_delta=min_delta,
    )


def write_requirement_comparison_report(
    comparisons: list[RequirementComparison],
    output_dir: str | Path,
) -> RequirementComparisonSuiteResult:
    """Write JSON and Markdown reports for requirement comparison results."""
    output_path = Path(output_dir).expanduser().resolve()
    output_path.mkdir(parents=True, exist_ok=True)
    summary = _comparison_summary(comparisons)
    json_path = output_path / "requirement-comparison-results.json"
    markdown_path = output_path / "requirement-comparison-results.md"
    json_path.write_text(
        json.dumps(
            {
                "summary": summary,
                "comparisons": [_comparison_to_dict(item) for item in comparisons],
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    markdown_path.write_text(_render_comparison_markdown(comparisons, summary), encoding="utf-8")
    return RequirementComparisonSuiteResult(
        output_dir=str(output_path),
        comparisons=comparisons,
        summary=summary,
        json_path=str(json_path),
        markdown_path=str(markdown_path),
    )


def run_direct_requirement_baseline(
    case: RequirementBenchmarkCase,
    *,
    output_dir: str | Path,
    config: LLMHTTPConfig,
    harness: OpenAICompatibleLLMHarness | None = None,
    prompt_mode: str = "plain",
) -> DirectRequirementBaselineResult:
    """Ask one model directly for a requirement spec without Conductor multi-agent workflow."""
    output_path = Path(output_dir).expanduser().resolve()
    output_path.mkdir(parents=True, exist_ok=True)
    direct_path = output_path / f"{case.id}.direct-requirement.md"
    runner = harness or OpenAICompatibleLLMHarness()
    result = runner.run(
        LLMHarnessRequest(
            prompt=build_direct_requirement_prompt(case.requirement, mode=prompt_mode),
            system_prompt=(
                "You are a direct single-model baseline. Do not simulate multi-agent review. "
                "Return a complete requirement specification in Chinese Markdown."
            ),
            working_directory=str(output_path),
            output_path=str(direct_path),
            config=config,
            max_tokens=4096,
            temperature=0.1,
            metadata={
                "case_id": case.id,
                "mode": "direct_requirement_baseline",
                "prompt_mode": prompt_mode,
            },
        )
    )
    if not result.success or not result.content.strip():
        raise RuntimeError(f"Direct requirement baseline failed: {result.error or 'empty output'}")
    direct_path.write_text(result.content, encoding="utf-8")
    return DirectRequirementBaselineResult(
        case_id=case.id,
        content=result.content,
        output_path=str(direct_path),
        model=result.model_name,
        duration_ms=result.duration_ms,
    )


def run_requirement_llm_preflight(
    *,
    backend: str,
    config: LLMHTTPConfig,
    output_dir: str | Path,
    harness: OpenAICompatibleLLMHarness | None = None,
) -> RequirementLLMPreflightResult:
    """Run a tiny request to verify that a benchmark LLM backend is reachable."""
    output_path = Path(output_dir).expanduser().resolve()
    output_path.mkdir(parents=True, exist_ok=True)
    runner = harness or OpenAICompatibleLLMHarness()
    result = runner.run(
        LLMHarnessRequest(
            prompt="Reply exactly: conductor-requirement-preflight-ok",
            system_prompt="You are a connectivity probe. Return only the requested text.",
            working_directory=str(output_path),
            output_path=str(output_path / f"{backend}.preflight.txt"),
            config=config,
            max_tokens=32,
            temperature=0,
            metadata={
                "mode": "requirement_benchmark_preflight",
                "backend": backend,
            },
        )
    )
    return RequirementLLMPreflightResult(
        backend=backend,
        success=result.success and bool(result.content.strip()),
        model=result.model_name,
        base_url=config.base_url,
        duration_ms=result.duration_ms,
        error=result.error,
        content=result.content.strip(),
    )


def build_direct_requirement_prompt(requirement: str, *, mode: str = "plain") -> str:
    """Build the non-platform baseline prompt for one requirement."""
    if mode == "structured":
        return (
            "请直接根据下面的用户需求产出一份完整需求规格说明，不要进行多 Agent 评审，"
            "不要提问，直接给出你认为最完整的版本。\n\n"
            "必须包含这些章节：目标、需求理解、范围边界、非目标、核心功能、数据与交互、"
            "验收标准、边界/异常场景、风险与假设、待确认问题、测试验证建议、下游交付约束。\n\n"
            f"用户需求：\n{requirement}\n"
        )
    return (
        "请直接根据下面的用户需求，输出你认为合适的需求说明。\n"
        "不要进行多 Agent 评审，不要模拟团队讨论，不要套用 Conductor 平台流程。\n"
        "不要反问；如果信息不足，可以自行做少量合理假设并写出来。\n"
        "请保持自然直出，结构和详略由你自行决定。\n\n"
        f"用户需求：\n{requirement}\n"
    )


def evaluate_requirement_artifact_from_manifest(
    manifest_path: str | Path,
    case: RequirementBenchmarkCase,
    *,
    min_score: int = 70,
) -> RequirementEvaluation:
    """Evaluate the best requirement/design artifact referenced by a Conductor manifest."""
    document = extract_requirement_document_from_manifest(manifest_path)
    return evaluate_requirement_document(document, case, min_score=min_score)


def extract_requirement_document_from_manifest(manifest_path: str | Path) -> str:
    """Read the preferred requirement-stage artifact from a run manifest."""
    path = Path(manifest_path)
    manifest: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
    artifacts = list(manifest.get("artifacts", []))
    preferred_kinds = ["frozen_requirement_spec", "collaboration_review", "requirement_spec", "design_overview"]
    for kind in preferred_kinds:
        for artifact in reversed(artifacts):
            if artifact.get("kind") != kind:
                continue
            artifact_path = Path(str(artifact.get("path", "")))
            if artifact_path.exists():
                return artifact_path.read_text(encoding="utf-8")
    raise FileNotFoundError(f"No requirement-stage artifact found in manifest: {path}")


def _coverage(expected: list[str], text: str) -> int:
    """Return percentage of expected terms found in text."""
    if not expected:
        return 100
    matched = sum(1 for term in expected if term.lower() in text)
    return int((matched / len(expected)) * 100)


def _aspect_coverage(expected_aspects: list[str], text: str) -> int:
    """Return percentage of required aspect labels covered by known terms."""
    if not expected_aspects:
        return 100
    matched = 0
    for aspect in expected_aspects:
        terms = ASPECT_TERMS.get(aspect, (aspect,))
        if _contains_any(text, terms):
            matched += 1
    return int((matched / len(expected_aspects)) * 100)


def _contains_any(text: str, terms: tuple[str, ...]) -> bool:
    """Return whether any required term appears in the document text."""
    return any(term.lower() in text for term in terms)


def _is_mock_or_placeholder_document(text: str) -> bool:
    """Return whether the document is platform scaffolding rather than a real requirement spec."""
    placeholder_terms = (
        "source backend: `mock",
        "source_backend: mock",
        "mock_fallback",
        "mock fallback",
        "模拟文档",
        "真实 llm",
        "真实后端",
        "由真实产物替换",
        "当前内容为模拟",
        "工作项执行说明",
    )
    return any(term in text for term in placeholder_terms)


def _extract_requirement_keywords(requirement: str, limit: int = 12) -> list[str]:
    """Extract stable domain keywords from free-form requirement text."""
    import re

    candidates = re.findall(r"[A-Za-z][A-Za-z0-9_-]{2,}|[\u4e00-\u9fff]{2,}", requirement)
    keywords: list[str] = []
    for candidate in candidates:
        normalized = candidate.strip().lower()
        if not normalized or normalized in STOPWORDS:
            continue
        if normalized not in [item.lower() for item in keywords]:
            keywords.append(candidate.strip())
        if len(keywords) >= limit:
            break
    return keywords


def _infer_required_aspects(requirement: str) -> list[str]:
    """Infer coarse quality aspects that should be covered by the requirement spec."""
    text = requirement.lower()
    aspects: list[str] = []
    for aspect, terms in ASPECT_TERMS.items():
        if _contains_any(text, terms):
            aspects.append(aspect)
    return aspects


def _comparison_summary(comparisons: list[RequirementComparison]) -> dict[str, int | float]:
    if not comparisons:
        return {
            "total": 0,
            "passed": 0,
            "platform_wins": 0,
            "direct_wins": 0,
            "ties": 0,
            "average_platform_score": 0.0,
            "average_direct_score": 0.0,
            "average_delta": 0.0,
        }
    return {
        "total": len(comparisons),
        "passed": sum(1 for item in comparisons if item.passed),
        "platform_wins": sum(1 for item in comparisons if item.winner == "platform"),
        "direct_wins": sum(1 for item in comparisons if item.winner == "direct"),
        "ties": sum(1 for item in comparisons if item.winner == "tie"),
        "average_platform_score": round(sum(item.platform.score for item in comparisons) / len(comparisons), 2),
        "average_direct_score": round(sum(item.direct.score for item in comparisons) / len(comparisons), 2),
        "average_delta": round(sum(item.delta for item in comparisons) / len(comparisons), 2),
    }


def _comparison_to_dict(comparison: RequirementComparison) -> dict[str, Any]:
    return {
        "case_id": comparison.case_id,
        "platform": _evaluation_to_dict(comparison.platform),
        "direct": _evaluation_to_dict(comparison.direct),
        "delta": comparison.delta,
        "winner": comparison.winner,
        "passed": comparison.passed,
        "min_score": comparison.min_score,
        "min_delta": comparison.min_delta,
    }


def _evaluation_to_dict(evaluation: RequirementEvaluation) -> dict[str, Any]:
    return {
        "case_id": evaluation.case_id,
        "score": evaluation.score,
        "passed": evaluation.passed,
        "checks": evaluation.checks,
        "metrics": evaluation.metrics,
        "findings": evaluation.findings,
    }


def _render_comparison_markdown(
    comparisons: list[RequirementComparison],
    summary: dict[str, int | float],
) -> str:
    lines = [
        "# Requirement Benchmark Comparison",
        "",
        f"- Total: {summary['total']}",
        f"- Passed: {summary['passed']}",
        f"- Platform Wins: {summary['platform_wins']}",
        f"- Direct Wins: {summary['direct_wins']}",
        f"- Average Platform Score: {summary['average_platform_score']}",
        f"- Average Direct Score: {summary['average_direct_score']}",
        f"- Average Delta: {summary['average_delta']}",
        "",
        "| Case | Platform | Direct | Delta | Winner | Passed |",
        "|---|---:|---:|---:|---|---|",
    ]
    for comparison in comparisons:
        lines.append(
            "| "
            f"{comparison.case_id} | "
            f"{comparison.platform.score} | "
            f"{comparison.direct.score} | "
            f"{comparison.delta} | "
            f"{comparison.winner} | "
            f"{'yes' if comparison.passed else 'no'} |"
        )
    lines.append("")
    for comparison in comparisons:
        lines.extend(
            [
                f"## {comparison.case_id}",
                "",
                f"- Platform findings: {'; '.join(comparison.platform.findings) or '-'}",
                f"- Direct findings: {'; '.join(comparison.direct.findings) or '-'}",
                "",
                "| Check | Platform | Direct |",
                "|---|---|---|",
            ]
        )
        check_names = sorted(set(comparison.platform.checks) | set(comparison.direct.checks))
        for check_name in check_names:
            lines.append(
                "| "
                f"{check_name} | "
                f"{'yes' if comparison.platform.checks.get(check_name) else 'no'} | "
                f"{'yes' if comparison.direct.checks.get(check_name) else 'no'} |"
            )
        lines.append("")
    return "\n".join(lines)


__all__ = [
    "RequirementBenchmarkCase",
    "RequirementComparison",
    "RequirementComparisonSuiteResult",
    "DirectRequirementBaselineResult",
    "RequirementLLMPreflightResult",
    "build_direct_requirement_prompt",
    "RequirementEvaluation",
    "build_requirement_case_from_text",
    "compare_requirement_documents",
    "default_requirement_benchmark_cases",
    "evaluate_requirement_artifact_from_manifest",
    "evaluate_requirement_document",
    "extract_requirement_document_from_manifest",
    "run_direct_requirement_baseline",
    "run_requirement_llm_preflight",
    "write_requirement_comparison_report",
]
