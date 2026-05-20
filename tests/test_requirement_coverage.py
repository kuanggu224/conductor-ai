"""Requirement coverage checks."""

from conductor.testing.coverage import evaluate_requirement_coverage, infer_coverage_rules


def test_infers_rules_from_chinese_requirement_terms() -> None:
    requirement = (
        "\u9700\u652f\u6301\u6dfb\u52a0\u4e66\u7c4d\uff0c"
        "\u5237\u65b0\u540e\u4fdd\u7559\u6570\u636e\uff0c"
        "\u5e76\u80fd\u5bfc\u51fa CSV\u3002"
    )

    rules = infer_coverage_rules(requirement)

    assert [rule.rule_id for rule in rules] == ["add_item", "persistence", "export_csv"]


def test_requirement_coverage_passes_when_harness_reports_required_evidence() -> None:
    requirement = (
        "\u7528\u6237\u53ef\u4ee5\u6dfb\u52a0\u6761\u76ee\uff0c"
        "\u5237\u65b0\u540e\u4fdd\u7559\u6570\u636e\uff0c"
        "\u5bfc\u51fa CSV\u3002"
    )
    output = "\n".join(
        [
            "Browser form interaction updated visible state: sample",
            "Browser reload preserved submitted values: sample",
            "Browser export/download action triggered",
        ]
    )

    result = evaluate_requirement_coverage(requirement, output)

    assert result.passed is True
    assert result.missing_rules == []
    assert [item.status for item in result.traceability] == ["covered", "covered", "covered"]
    assert result.traceability[1].requirement_terms == ["\u5237\u65b0\u540e", "\u4fdd\u7559\u6570\u636e"]
    assert result.traceability[1].evidence_terms == ["browser reload preserved submitted values"]
    assert "Status: `pass`" in result.render_markdown()
    assert "Requirement Traceability" in result.render_markdown()


def test_requirement_coverage_fails_missing_persistence_evidence() -> None:
    requirement = "\u7528\u6237\u6dfb\u52a0\u6570\u636e\u540e\uff0c\u5237\u65b0\u540e\u9700\u8981\u4fdd\u7559\u6570\u636e\u3002"
    output = "Browser form interaction updated visible state: sample"

    result = evaluate_requirement_coverage(requirement, output)

    assert result.passed is False
    assert [rule.rule_id for rule in result.missing_rules] == ["persistence"]
    assert [item.status for item in result.traceability] == ["covered", "missing"]
    assert result.traceability[1].requirement_terms == ["\u5237\u65b0\u540e", "\u4fdd\u7559\u6570\u636e"]
    assert result.traceability[1].evidence_terms == []
    assert result.summary() == "Requirement coverage missing: refresh persistence"


def test_requirement_coverage_does_not_infer_filter_from_input_sanitization() -> None:
    requirement = "\u4e66\u540d\u5305\u542b\u6362\u884c\u7b26\u65f6\uff0c\u524d\u7aef\u81ea\u52a8\u8fc7\u6ee4\u5e76\u53bb\u9664\u3002"

    rules = infer_coverage_rules(requirement)

    assert "filter" not in [rule.rule_id for rule in rules]


def test_requirement_coverage_still_infers_filter_interaction() -> None:
    requirement = "\u9875\u9762\u9700\u652f\u6301\u6309\u72b6\u6001\u7b5b\u9009\u6761\u76ee\u3002"

    rules = infer_coverage_rules(requirement)

    assert "filter" in [rule.rule_id for rule in rules]


def test_requirement_coverage_infers_filter_interaction_from_search_terms() -> None:
    requirement = "\u9875\u9762\u9700\u652f\u6301\u6309\u5173\u952e\u8bcd\u641c\u7d22\u6761\u76ee\u3002"

    result = evaluate_requirement_coverage(requirement, "Browser filter interaction changed visible results")

    assert result.passed is True
    assert [rule.rule_id for rule in result.required_rules] == ["filter"]
    assert result.traceability[0].requirement_terms == ["\u641c\u7d22", "\u5173\u952e\u8bcd"]


def test_requirement_coverage_requires_explicit_filter_interaction_evidence() -> None:
    requirement = "\u9875\u9762\u9700\u652f\u6301\u6309\u72b6\u6001\u7b5b\u9009\u6761\u76ee\u3002"

    missing = evaluate_requirement_coverage(requirement, "filter control exists")
    covered = evaluate_requirement_coverage(requirement, "Browser filter interaction changed visible results")

    assert missing.passed is False
    assert [rule.rule_id for rule in missing.missing_rules] == ["filter"]
    assert covered.passed is True
    assert covered.traceability[0].evidence_terms == ["browser filter interaction changed visible results"]


def test_requirement_coverage_infers_delete_interaction() -> None:
    requirement = "\u9875\u9762\u9700\u652f\u6301\u5220\u9664\u6761\u76ee\u3002"

    missing = evaluate_requirement_coverage(requirement, "Browser form interaction updated visible state: sample")
    covered = evaluate_requirement_coverage(requirement, "Browser delete interaction removed visible item")

    assert [rule.rule_id for rule in missing.required_rules] == ["delete_item"]
    assert missing.passed is False
    assert covered.passed is True
    assert covered.traceability[0].evidence_terms == ["browser delete interaction removed visible item"]


def test_requirement_coverage_infers_file_import_interaction() -> None:
    requirement = "\u9875\u9762\u9700\u652f\u6301\u5bfc\u5165 CSV \u6587\u4ef6\u5e76\u89e3\u6790\u6761\u76ee\u3002"

    missing = evaluate_requirement_coverage(requirement, "Browser form interaction updated visible state: sample")
    covered = evaluate_requirement_coverage(requirement, "Browser file import processed sample file")

    assert [rule.rule_id for rule in missing.required_rules] == ["file_import"]
    assert [rule.rule_id for rule in missing.missing_rules] == ["file_import"]
    assert covered.passed is True
    assert covered.traceability[0].evidence_terms == ["browser file import processed sample file"]


def test_requirement_coverage_infers_api_behavior_interaction() -> None:
    requirement = "\u9700\u5b9e\u73b0\u540e\u7aef API \u63a5\u53e3\uff0c\u652f\u6301\u521b\u5efa\u548c\u67e5\u8be2\u6761\u76ee\u3002"

    missing = evaluate_requirement_coverage(requirement, "3 passed")
    covered = evaluate_requirement_coverage(requirement, "API validation exercised endpoint behavior")

    assert [rule.rule_id for rule in missing.required_rules] == ["api_behavior"]
    assert [rule.rule_id for rule in missing.missing_rules] == ["api_behavior"]
    assert covered.passed is True
    assert covered.traceability[0].evidence_terms == ["api validation exercised endpoint behavior"]


def test_requirement_coverage_does_not_infer_api_from_backend_negation() -> None:
    requirement = "\u53ea\u505a\u524d\u7aef\u9759\u6001\u9875\u9762\uff0c\u4e0d\u63a5 API\uff0c\u4e0d\u9700\u8981\u540e\u7aef\u3002"

    rules = infer_coverage_rules(requirement)

    assert "api_behavior" not in [rule.rule_id for rule in rules]
