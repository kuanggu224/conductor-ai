"""Design-stage quality gate tests."""

from conductor.design_quality import evaluate_design_document


def test_design_quality_scores_actionable_design_higher_than_weak_draft() -> None:
    weak = "可以做一个页面。"
    strong = """
    # 总体设计

    ## 目标
    交付个人读书清单静态 Web 应用。

    ## 需求理解
    用户需要新增书名、作者、阅读状态和评分，并能筛选和导出 CSV。

    ## 范围边界
    范围是本地单用户页面；非目标是不接后端、不做登录。

    ## 方案
    架构由页面组件、列表模块、CSV 导出模块组成，接口边界是浏览器本地事件。

    ## 数据与状态
    字段包括 title、author、status、rating，状态存储在 localStorage。

    ## 验收与测试
    测试新增、筛选、导出、刷新保留数据，以及空输入异常错误。

    ## 风险与假设
    风险是 localStorage 被清理；假设只支持单浏览器。
    """

    weak_result = evaluate_design_document(weak)
    strong_result = evaluate_design_document(strong)

    assert weak_result.passed is False
    assert strong_result.passed is True
    assert strong_result.score > weak_result.score
    assert strong_result.dimension_scores["architecture"] >= 50


def test_design_quality_accepts_actionable_english_design() -> None:
    document = """
    # Overall Design

    ## Goal
    Deliver a browser-only study tracker.

    ## Requirement Understanding
    Users add cards, filter by topic and status, persist data, delete cards, and export CSV.

    ## Scope Boundary
    In scope is one static web page. Non-goals are backend services, server APIs, login/auth, accounts, and cloud sync.

    ## Solution
    Architecture uses index.html, app.js, and styles.css. Components include form, filter toolbar, list, empty state, and export button.
    The flow is load storage, render page, validate submit, persist state, filter visible cards, and export CSV.

    ## Data And State
    Data fields are id, question, answer, topic, status, and createdAt. State lives in memory and localStorage.

    ## Acceptance And Test Plan
    Test creation, validation error, filter behavior, delete persistence, reload persistence, and CSV export.

    ## Risks And Assumptions
    Risk: localStorage can be cleared. Assumption: single-user local browser use. Open question: exact status vocabulary.
    """

    result = evaluate_design_document(document)

    assert result.passed is True
    assert result.score >= 70
