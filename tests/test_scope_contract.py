"""Frozen requirement scope contract tests."""

from conductor.artifacts.scope_contract import evaluate_scope_contract, infer_scope_rules


def test_scope_contract_infers_hard_non_goals() -> None:
    requirement = """
    ## 范围边界
    仅实现前端静态页面，不接后端，不接数据库。
    ## 非目标
    不做登录、注册、云同步和 CSV 导入。
    """

    rules = infer_scope_rules(requirement)

    assert {rule.rule_id for rule in rules} >= {"no_backend", "no_database", "no_login", "no_cloud_sync", "no_import"}


def test_scope_contract_allows_repeating_non_goal_language() -> None:
    requirement = "非目标：不接后端，不做登录。"
    candidate = "范围边界：继续保持不接后端，不做登录，只使用 localStorage。"

    result = evaluate_scope_contract(requirement, candidate)

    assert result.passed is True


def test_scope_contract_flags_positive_scope_expansion() -> None:
    requirement = "非目标：不接后端，不做登录。"
    candidate = "方案：新增 FastAPI endpoint，并实现 login token session 管理。"

    result = evaluate_scope_contract(requirement, candidate)

    assert result.passed is False
    assert {item.rule_id for item in result.violations} == {"no_login", "no_backend"}


def test_scope_contract_ignores_artifact_metadata() -> None:
    requirement = "非目标：不接后端，不做登录。"
    candidate = """
    - Source Backend: `llm_harness/model`
    - Collaboration Session ID: `session-123`

    ## 范围边界
    继续保持不接后端，不做登录。
    """

    result = evaluate_scope_contract(requirement, candidate)

    assert result.passed is True


def test_scope_contract_does_not_match_author_or_generic_interface_heading() -> None:
    requirement = "非目标：不接后端，不做登录。"
    candidate = """
    ## 接口与数据关注点
    数据结构：每本书为对象 `{title, author, status, rating, note}`。
    """

    result = evaluate_scope_contract(requirement, candidate)

    assert result.passed is True
