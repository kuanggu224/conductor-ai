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


def test_scope_contract_allows_localstorage_api_and_sync_wording() -> None:
    requirement = "Non-goals: no backend API, no cloud sync."
    candidate = """
    ## Validation
    - Add item updates the list and writes to localStorage synchronously.
    - Use the localStorage API to assert browser persistence.
    - No network request is introduced.
    """

    result = evaluate_scope_contract(requirement, candidate)

    assert result.passed is True


def test_scope_contract_allows_local_frontend_api_event_registration_and_storage_sync() -> None:
    requirement = "非目标：不接后端，不做登录注册，不做云同步。"
    candidate = """
    ## 方案
    - JavaScript 使用原生 API 操作 DOM 和 localStorage。
    - 初始化时读取本地数据并注册事件监听。
    - 多标签页通过 storage 事件同步本地数据。
    """

    result = evaluate_scope_contract(requirement, candidate)

    assert result.passed is True


def test_scope_contract_still_flags_backend_api_and_remote_sync() -> None:
    requirement = "Non-goals: no backend API, no cloud sync."
    candidate = """
    ## Plan
    Add a FastAPI endpoint and remote sync service for cross-device backup.
    """

    result = evaluate_scope_contract(requirement, candidate)

    assert result.passed is False
    assert {item.rule_id for item in result.violations} == {"no_backend", "no_cloud_sync"}
