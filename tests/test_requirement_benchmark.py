"""Requirement-stage benchmark tests."""

import json
from pathlib import Path

from conductor.requirement_benchmark import (
    RequirementBenchmarkCase,
    build_direct_requirement_prompt,
    compare_requirement_documents,
    default_requirement_benchmark_cases,
    evaluate_requirement_artifact_from_manifest,
    evaluate_requirement_document,
    build_requirement_case_from_text,
    run_direct_requirement_baseline,
    run_requirement_llm_preflight,
    write_requirement_comparison_report,
)
from conductor.agents.llm import LLMHTTPConfig
from conductor.harness.llm import LLMHarnessResult


class FakeDirectRequirementHarness:
    def __init__(self) -> None:
        self.requests = []

    def run(self, request):
        self.requests.append(request)
        return LLMHarnessResult(
            success=True,
            content="目标：读书清单。范围边界：书名、作者、状态、评分、备注、CSV 导出。验收标准：新增、筛选、导出。风险与假设：CSV 编码。测试验证建议：验证持久化。",
            duration_ms=12,
            model_name=request.config.model_name,
            output_path=request.output_path,
        )


class FakePreflightHarness:
    def __init__(self, *, success: bool = True) -> None:
        self.success = success
        self.requests = []

    def run(self, request):
        self.requests.append(request)
        return LLMHarnessResult(
            success=self.success,
            content="conductor-requirement-preflight-ok" if self.success else "",
            duration_ms=3,
            model_name=request.config.model_name,
            output_path=request.output_path,
            error="" if self.success else "connection refused",
        )


def test_requirement_evaluator_scores_complete_document_higher() -> None:
    case = default_requirement_benchmark_cases()[0]
    weak = "做一个读书清单，支持书名和作者。"
    strong = """
    # 目标
    用户需要管理个人读书清单，包含书名、作者、阅读状态、评分、备注。

    # 范围与非目标
    范围包括新增、编辑、删除、按状态筛选、导出 CSV、刷新后保留数据。
    非目标：不做账号登录和云同步。

    # 页面、数据与交互
    页面包含表单、列表、筛选控件和导出按钮。数据保存在 localStorage。

    # 验收标准
    - 添加一本书后，列表显示书名、作者、状态、评分、备注。
    - 刷新页面后，保留数据仍可见。
    - 选择状态筛选时，只显示对应状态。
    - 点击导出 CSV 后，文件包含当前书目数据。

    # 风险、假设与待确认问题
    假设评分为 1-5。风险是 CSV 编码和空列表导出行为需要明确。

    # 测试验证
    验证新增、筛选、刷新保留数据和 CSV 导出。

    # 下游交付约束
    后续设计、开发、测试必须以本需求基线为准，不得扩展到账号体系。
    """

    weak_score = evaluate_requirement_document(weak, case)
    strong_score = evaluate_requirement_document(strong, case)

    assert strong_score.score > weak_score.score
    assert strong_score.passed is True
    assert weak_score.passed is False
    assert strong_score.checks["has_non_goals"] is True
    assert strong_score.checks["has_open_questions_or_assumptions"] is True
    assert strong_score.checks["has_edge_cases"] is True
    assert strong_score.checks["has_downstream_constraints"] is True


def test_requirement_evaluator_penalizes_mock_placeholder_document() -> None:
    case = default_requirement_benchmark_cases()[0]
    document = (
        "# 执行说明文档\n\n"
        "- Source Backend: `mock_fallback`\n\n"
        "## 目标\n个人读书清单。\n"
        "## 范围\n书名、作者、状态、评分、CSV 导出、刷新后保留数据。\n"
        "## 验收标准\n新增、筛选、导出、持久化。\n"
        "## 风险\n当前内容为模拟文档，需要在真实 LLM 或 CLI backend 接入后由真实产物替换。\n"
        "## 测试\n验证新增、筛选、导出和存储。\n"
    )

    evaluation = evaluate_requirement_document(document, case)

    assert evaluation.score <= 35
    assert evaluation.passed is False
    assert evaluation.checks["not_mock_or_placeholder"] is False
    assert any("placeholder" in finding for finding in evaluation.findings)


def test_requirement_evaluator_maps_aspect_labels_to_chinese_terms() -> None:
    case = default_requirement_benchmark_cases()[0]
    document = """
    目标：个人读书清单。
    范围：书名、作者、阅读状态、评分、备注、状态筛选、CSV 导出、刷新后保留数据。
    页面和交互：表单、列表、筛选控件、导出按钮。
    数据：使用 localStorage 存储。
    验收标准：新增、筛选、导出 CSV、刷新保留数据。
    风险与待确认：CSV 编码和评分范围。
    测试：验证页面交互、筛选、持久化和导出。
    """

    evaluation = evaluate_requirement_document(document, case)

    assert evaluation.metrics["aspect_coverage"] == 100
    assert evaluation.checks["aspect_coverage"] is True


def test_requirement_evaluator_counts_translated_domain_keyword_aliases() -> None:
    expense_case = default_requirement_benchmark_cases()[1]
    expense_document = """
    目标：员工发起报销申请，经理完成审批。
    范围边界：支持费用提交、审批通过、驳回、审批状态跟踪和无效输入校验；非目标是不接入真实财务系统。
    接口与数据：记录员工、金额、事由、票据说明、当前状态和错误提示。
    验收标准：提交后进入待审批；经理可以批准或拒绝；非法金额返回清晰错误。
    风险与假设：角色权限先用本地模拟。待确认问题：是否需要多级审批。
    边界/异常场景：空金额、负数金额、重复提交、已审批记录再次提交。
    测试验证：覆盖提交、批准、拒绝、状态展示和错误提示。
    下游交付约束：后续设计、开发、测试必须保持审批状态机一致。
    """
    csv_case = default_requirement_benchmark_cases()[2]
    csv_document = """
    目标：本地 CSV 清洗工具。
    范围边界：读取表格文件，去除首尾空格，删除重复行，标记无效行，并导出清洗后的文件；非目标是不做云端存储。
    数据与交互：展示原始行数、重复行数量、异常行列表和导出入口。
    验收标准：空白被修剪；重复数据被去重；非法行有错误提示；用户可以下载输出文件。
    风险与假设：默认 UTF-8。待确认问题：大文件上限。
    边界/异常场景：空文件、缺失表头、坏行、全部重复。
    测试验证：覆盖读取、去空格、去重、错误报告和导出。
    下游交付约束：实现不能依赖外部服务。
    """

    expense = evaluate_requirement_document(expense_document, expense_case)
    csv = evaluate_requirement_document(csv_document, csv_case)

    assert expense.metrics["keyword_coverage"] == 100
    assert expense.checks["keyword_coverage"] is True
    assert csv.metrics["keyword_coverage"] == 100
    assert csv.checks["keyword_coverage"] is True


def test_requirement_evaluator_counts_chinese_keyword_variants() -> None:
    case = RequirementBenchmarkCase(
        id="chinese-variants",
        name="Chinese Keyword Variants",
        requirement="阅读清单需要书名、保留数据和筛选。",
        expected_keywords=["书名", "保留数据", "筛选"],
    )
    document = """
    目标：用户维护个人阅读清单。
    范围：表单字段使用图书标题，数据保存到 localStorage，列表支持按条件过滤。
    验收标准：刷新后仍可见，选择过滤条件后列表更新。
    风险与假设：本地存储容量有限。
    测试验证：覆盖新增、过滤和刷新后仍可见。
    """

    evaluation = evaluate_requirement_document(document, case)

    assert evaluation.metrics["keyword_coverage"] == 100
    assert evaluation.metrics["matched_keyword_count"] == 3
    assert evaluation.metrics["expected_keyword_count"] == 3
    assert evaluation.checks["keyword_coverage"] is True


def test_requirement_evaluator_reports_keyword_match_evidence() -> None:
    case = RequirementBenchmarkCase(
        id="semantic-aliases",
        name="Semantic Aliases",
        requirement="Expense approval and CSV cleaner",
        expected_keywords=["expense", "submit", "approve", "reject", "CSV", "trim", "duplicate", "invalid", "export"],
    )
    document = """
    目标：费用流程和逗号分隔文件清洗。
    范围：员工可以发起申请，经理同意或驳回；文件处理会去除首尾空白，删除重复行，标记坏行。
    验收标准：审批状态可见，清洗结果可以生成文件下载。
    风险与假设：CSV 编码和金额格式需要确认。
    测试验证：覆盖提交、批准、拒绝、无效行和导出。
    """

    evaluation = evaluate_requirement_document(document, case)

    assert evaluation.metrics["keyword_coverage"] == 100
    assert evaluation.metrics["missing_keywords"] == []
    assert evaluation.metrics["keyword_matches"]["expense"] == "费用"
    assert evaluation.metrics["keyword_matches"]["CSV"] in {"csv", "逗号分隔文件"}
    assert "duplicate" in evaluation.metrics["matched_keywords"]


def test_requirement_evaluator_reports_missing_keywords() -> None:
    case = RequirementBenchmarkCase(
        id="missing-keywords",
        name="Missing Keywords",
        requirement="CSV cleaner",
        expected_keywords=["CSV", "去重", "无效"],
    )
    document = "目标：读取 CSV。范围：只做文件读取。验收标准：可以打开文件。"

    evaluation = evaluate_requirement_document(document, case)

    assert evaluation.metrics["matched_keyword_count"] == 1
    assert evaluation.metrics["expected_keyword_count"] == 3
    assert any("Missing keywords: 去重, 无效" in finding for finding in evaluation.findings)


def test_requirement_case_extracts_chinese_domain_terms_from_continuous_text() -> None:
    case = build_requirement_case_from_text(
        "zh-freeform",
        "\u505a\u4e00\u4e2a\u8bfb\u4e66\u6e05\u5355\uff0c"
        "\u652f\u6301\u6dfb\u52a0\u4e66\u540d\u4f5c\u8005\u548c\u8bc4\u5206\uff0c"
        "\u6309\u9605\u8bfb\u72b6\u6001\u7b5b\u9009\uff0c"
        "\u5237\u65b0\u540e\u4fdd\u7559\u6570\u636e\uff0c"
        "\u5e76\u5bfc\u51fa CSV\u3002",
    )
    document = """
    \u76ee\u6807\uff1a\u4e2a\u4eba\u8bfb\u4e66\u6e05\u5355\u3002
    \u8303\u56f4\uff1a\u7528\u6237\u53ef\u4ee5\u65b0\u589e\u4e66\u540d\u3001\u4f5c\u8005\u548c\u8bc4\u5206\uff0c\u6309\u9605\u8bfb\u72b6\u6001\u8fc7\u6ee4\uff0c\u4f7f\u7528 localStorage \u4fdd\u5b58\u3002
    \u9a8c\u6536\u6807\u51c6\uff1a\u5237\u65b0\u540e\u4fdd\u7559\u6570\u636e\uff0c\u70b9\u51fb\u5bfc\u51fa CSV \u53ef\u4e0b\u8f7d\u3002
    \u98ce\u9669\u4e0e\u5047\u8bbe\uff1aCSV \u7f16\u7801\u9700\u8981\u786e\u8ba4\u3002
    \u6d4b\u8bd5\u9a8c\u8bc1\uff1a\u8986\u76d6\u65b0\u589e\u3001\u7b5b\u9009\u3001\u6301\u4e45\u5316\u548c\u5bfc\u51fa\u3002
    """

    evaluation = evaluate_requirement_document(document, case)

    assert "\u6dfb\u52a0" in case.expected_keywords
    assert "\u5237\u65b0\u540e" in case.expected_keywords
    assert "\u5bfc\u51fa" in case.expected_keywords
    assert evaluation.metrics["keyword_coverage"] >= 70
    assert evaluation.checks["keyword_coverage"] is True


def test_requirement_comparison_requires_platform_delta() -> None:
    case = default_requirement_benchmark_cases()[0]
    platform_doc = """
    目标：个人读书清单 Web 应用。范围：书名、作者、阅读状态、评分、备注、状态筛选、CSV 导出、刷新后保留数据。
    非目标：不做登录。页面和数据：使用表单、列表、筛选、localStorage、CSV export。
    验收标准：新增后显示字段；刷新保留数据；按状态筛选；导出 CSV 包含书名作者状态评分备注。
    风险和待确认：评分范围、CSV 编码。测试：验证新增、筛选、持久化、导出。
    """
    direct_doc = "做一个读书清单 Web 应用，包含书名、作者、状态。"

    comparison = compare_requirement_documents(
        case=case,
        platform_document=platform_doc,
        direct_document=direct_doc,
        min_delta=5,
    )

    assert comparison.winner == "platform"
    assert comparison.delta >= 5
    assert comparison.passed is True


def test_requirement_evaluator_reads_manifest_artifact(tmp_path) -> None:
    case = default_requirement_benchmark_cases()[0]
    artifact_path = tmp_path / "artifact.md"
    artifact_path.write_text(
        """
        目标：个人读书清单。范围：书名、作者、阅读状态、评分、备注、状态筛选、CSV 导出、刷新后保留数据。
        页面、数据与交互：表单、列表、筛选、localStorage。
        验收标准：添加、筛选、导出 CSV、刷新保留数据均可验证。
        风险与待确认问题：CSV 编码、评分范围。测试：覆盖新增、筛选、持久化、导出。
        """,
        encoding="utf-8",
    )
    manifest_path = tmp_path / "manifest.json"
    manifest_path.write_text(
        json.dumps(
            {
                "artifacts": [
                    {"kind": "design_overview", "path": str(tmp_path / "old.md")},
                    {"kind": "collaboration_review", "path": str(artifact_path)},
                ]
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    evaluation = evaluate_requirement_artifact_from_manifest(manifest_path, case)

    assert evaluation.passed is True
    assert evaluation.checks["has_acceptance_criteria"] is True


def test_requirement_comparison_report_writes_json_and_markdown(tmp_path) -> None:
    case = default_requirement_benchmark_cases()[0]
    comparison = compare_requirement_documents(
        case=case,
        platform_document=(
            "目标：读书清单。范围：书名 作者 状态 评分 CSV 保留数据。"
            "验收标准：新增、筛选、导出、刷新保留。风险：CSV 编码。测试：验证交互。"
        ),
        direct_document="读书清单，书名作者状态。",
    )

    result = write_requirement_comparison_report([comparison], tmp_path)

    assert result.summary["total"] == 1
    assert result.markdown_path.endswith("requirement-comparison-results.md")
    assert Path(result.json_path).exists()
    assert Path(result.markdown_path).exists()
    markdown = Path(result.markdown_path).read_text(encoding="utf-8")
    assert "| Check | Platform | Direct |" in markdown
    assert "has_acceptance_criteria" in markdown


def test_direct_requirement_baseline_runs_through_harness(tmp_path) -> None:
    case = default_requirement_benchmark_cases()[0]
    harness = FakeDirectRequirementHarness()

    result = run_direct_requirement_baseline(
        case,
        output_dir=tmp_path,
        config=LLMHTTPConfig(
            base_url="http://127.0.0.1:1234/v1",
            model_name="direct-model",
            enabled=True,
        ),
        harness=harness,
    )

    assert result.model == "direct-model"
    assert result.duration_ms == 12
    assert Path(result.output_path).exists()
    assert harness.requests[0].metadata["mode"] == "direct_requirement_baseline"
    assert harness.requests[0].metadata["prompt_mode"] == "plain"
    assert "多 Agent" in harness.requests[0].prompt


def test_direct_requirement_prompt_is_non_platform_baseline() -> None:
    prompt = build_direct_requirement_prompt("做一个读书清单")
    structured = build_direct_requirement_prompt("做一个读书清单", mode="structured")

    assert "不要进行多 Agent 评审" in prompt
    assert "用户需求" in prompt
    assert "边界/异常场景" not in prompt
    assert "下游交付约束" not in prompt
    assert "边界/异常场景" in structured
    assert "下游交付约束" in structured


def test_requirement_llm_preflight_reports_backend_status(tmp_path) -> None:
    harness = FakePreflightHarness()

    result = run_requirement_llm_preflight(
        backend="local",
        config=LLMHTTPConfig(
            base_url="http://127.0.0.1:1234/v1",
            model_name="preflight-model",
            enabled=True,
        ),
        output_dir=tmp_path,
        harness=harness,
    )

    assert result.success is True
    assert result.backend == "local"
    assert result.model == "preflight-model"
    assert harness.requests[0].metadata["mode"] == "requirement_benchmark_preflight"


def test_requirement_llm_preflight_preserves_failure_reason(tmp_path) -> None:
    result = run_requirement_llm_preflight(
        backend="local",
        config=LLMHTTPConfig(
            base_url="http://127.0.0.1:1234/v1",
            model_name="preflight-model",
            enabled=True,
        ),
        output_dir=tmp_path,
        harness=FakePreflightHarness(success=False),
    )

    assert result.success is False
    assert result.error == "connection refused"
