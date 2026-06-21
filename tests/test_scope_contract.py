"""Frozen requirement scope contract tests."""

from conductor.artifacts.scope_contract import evaluate_scope_contract, infer_scope_rules


def test_scope_contract_infers_hard_non_goals() -> None:
    requirement = """
    Scope boundary: no backend API and no database.
    Non-goals: no login, no cloud sync, no CSV import.
    """

    rules = infer_scope_rules(requirement)

    assert {rule.rule_id for rule in rules} >= {"no_backend", "no_database", "no_login", "no_cloud_sync", "no_import"}


def test_scope_contract_allows_repeating_non_goal_language() -> None:
    requirement = "Non-goals: no backend API and no login."
    candidate = "Scope boundary: continue to avoid backend API and login."

    result = evaluate_scope_contract(requirement, candidate)

    assert result.passed is True


def test_scope_contract_allows_excluded_coverage_trace_lines() -> None:
    requirement = "Out of scope: backend API, CSV export, file import."
    candidate = "- Excluded: CSV export/download, file import/upload, backend/API integration"

    result = evaluate_scope_contract(requirement, candidate)

    assert result.passed is True


def test_scope_contract_flags_positive_scope_expansion() -> None:
    requirement = "Non-goals: no backend API and no login."
    candidate = "Plan: add a FastAPI endpoint and implement login token sessions."

    result = evaluate_scope_contract(requirement, candidate)

    assert result.passed is False
    assert {item.rule_id for item in result.violations} == {"no_login", "no_backend"}


def test_scope_contract_ignores_artifact_metadata() -> None:
    requirement = "Non-goals: no backend API and no login."
    candidate = """
    - Source Backend: `llm_harness/model`
    - Collaboration Session ID: `session-123`

    Scope boundary: continue to avoid backend API and login.
    """

    result = evaluate_scope_contract(requirement, candidate)

    assert result.passed is True


def test_scope_contract_does_not_match_author_or_generic_data_heading() -> None:
    requirement = "Non-goals: no login."
    candidate = "Data structure: each book has `{title, author, status, rating, note}`."

    result = evaluate_scope_contract(requirement, candidate)

    assert result.passed is True


def test_scope_contract_flags_api_usage_when_backend_api_is_excluded() -> None:
    requirement = "Non-goals: no backend API."
    candidate = "Validation: call the API endpoint and assert persisted resource state."

    result = evaluate_scope_contract(requirement, candidate)

    assert result.passed is False
    assert {item.rule_id for item in result.violations} == {"no_backend"}


def test_scope_contract_ignores_mock_runtime_backend_notes() -> None:
    requirement = "Non-goals: no backend API, no login."
    candidate = """
    Scope boundary: no backend API and no login are part of the product scope.
    Risk: current content is a mock artifact and must be replaced when real backend execution is enabled.
    """

    result = evaluate_scope_contract(requirement, candidate)

    assert result.passed is True


def test_scope_contract_still_flags_backend_api_and_remote_sync() -> None:
    requirement = "Non-goals: no backend API, no cloud sync."
    candidate = "Plan: add a FastAPI endpoint and remote sync service for cross-device backup."

    result = evaluate_scope_contract(requirement, candidate)

    assert result.passed is False
    assert {item.rule_id for item in result.violations} == {"no_backend", "no_cloud_sync"}
