"""Requirement acceptance coverage checks for test reports."""

from __future__ import annotations

from dataclasses import dataclass, field

from conductor.domain.models import Artifact


@dataclass(frozen=True, slots=True)
class CoverageRule:
    """One requirement-to-validation signal rule."""

    rule_id: str
    label: str
    requirement_terms: tuple[str, ...]
    evidence_terms: tuple[str, ...]


@dataclass(slots=True)
class CoverageTraceItem:
    """Trace one requirement concern to validation evidence."""

    rule_id: str
    label: str
    status: str
    requirement_terms: list[str] = field(default_factory=list)
    evidence_terms: list[str] = field(default_factory=list)


@dataclass(slots=True)
class CoverageResult:
    """Requirement coverage result."""

    required_rules: list[CoverageRule] = field(default_factory=list)
    covered_rule_ids: list[str] = field(default_factory=list)
    missing_rules: list[CoverageRule] = field(default_factory=list)
    traceability: list[CoverageTraceItem] = field(default_factory=list)

    @property
    def passed(self) -> bool:
        return not self.missing_rules

    def render_markdown(self) -> str:
        """Render a compact coverage section for test reports."""
        if not self.required_rules:
            return "## Requirement Coverage\n\n- No frozen requirement coverage rules inferred.\n"

        covered = ", ".join(rule.label for rule in self.required_rules if rule.rule_id in self.covered_rule_ids) or "-"
        missing = ", ".join(rule.label for rule in self.missing_rules) or "-"
        status = "pass" if self.passed else "missing_coverage"
        trace_lines = [
            "| Rule | Status | Requirement Signal | Validation Evidence |",
            "|---|---|---|---|",
        ]
        for item in self.traceability:
            requirement_terms = ", ".join(item.requirement_terms) or "-"
            evidence_terms = ", ".join(item.evidence_terms) or "-"
            trace_lines.append(f"| {item.label} | `{item.status}` | {requirement_terms} | {evidence_terms} |")
        return (
            "## Requirement Coverage\n\n"
            f"- Status: `{status}`\n"
            f"- Covered: {covered}\n"
            f"- Missing: {missing}\n"
            "\n"
            "### Requirement Traceability\n"
            + "\n".join(trace_lines)
            + "\n"
        )

    def summary(self) -> str:
        """Return a compact failure summary."""
        if self.passed:
            return "Requirement coverage passed"
        labels = ", ".join(rule.label for rule in self.missing_rules)
        return f"Requirement coverage missing: {labels}"


COVERAGE_RULES: tuple[CoverageRule, ...] = (
    CoverageRule(
        rule_id="add_item",
        label="add item interaction",
        requirement_terms=(
            "\u6dfb\u52a0",
            "\u65b0\u589e",
            "\u521b\u5efa",
            "add",
            "create",
        ),
        evidence_terms=("browser form interaction updated visible state",),
    ),
    CoverageRule(
        rule_id="persistence",
        label="refresh persistence",
        requirement_terms=(
            "\u5237\u65b0\u540e",
            "\u6301\u4e45\u5316",
            "\u4fdd\u7559\u6570\u636e",
            "\u4fdd\u5b58",
            "localstorage",
            "local storage",
            "persist",
        ),
        evidence_terms=(
            "browser reload preserved submitted values",
            "browser localstorage changed after form submit",
        ),
    ),
    CoverageRule(
        rule_id="export_csv",
        label="CSV export/download",
        requirement_terms=(
            "csv",
            "\u5bfc\u51fa",
            "\u4e0b\u8f7d",
            "export",
            "download",
        ),
        evidence_terms=("browser export/download action triggered",),
    ),
    CoverageRule(
        rule_id="filter",
        label="filter interaction",
        requirement_terms=(
            "\u7b5b\u9009",
            "\u8fc7\u6ee4",
            "\u641c\u7d22",
            "\u5173\u952e\u8bcd",
            "filter",
            "search",
            "query",
        ),
        evidence_terms=("browser filter interaction changed visible results",),
    ),
)


def evaluate_requirement_coverage(frozen_requirement: Artifact | str | None, validation_output: str) -> CoverageResult:
    """Evaluate whether validation output covers key frozen requirement terms."""
    if frozen_requirement is None:
        return CoverageResult()

    requirement_text = frozen_requirement.content if isinstance(frozen_requirement, Artifact) else frozen_requirement
    required_rules = infer_coverage_rules(requirement_text)
    output = validation_output.lower()
    covered_rule_ids = [
        rule.rule_id
        for rule in required_rules
        if any(term.lower() in output for term in rule.evidence_terms)
    ]
    missing_rules = [rule for rule in required_rules if rule.rule_id not in set(covered_rule_ids)]
    return CoverageResult(
        required_rules=required_rules,
        covered_rule_ids=covered_rule_ids,
        missing_rules=missing_rules,
        traceability=[
            CoverageTraceItem(
                rule_id=rule.rule_id,
                label=rule.label,
                status="covered" if rule.rule_id in covered_rule_ids else "missing",
                requirement_terms=_matched_terms(rule.requirement_terms, requirement_text),
                evidence_terms=_matched_terms(rule.evidence_terms, output),
            )
            for rule in required_rules
        ],
    )


def infer_coverage_rules(requirement_text: str) -> list[CoverageRule]:
    """Infer coverage rules from frozen requirement text."""
    normalized = requirement_text.lower()
    return [
        rule
        for rule in COVERAGE_RULES
        if _rule_is_required(rule, normalized)
    ]


def build_testing_checklist(requirement_text: str) -> list[dict[str, object]]:
    """Build machine-readable testing checklist entries from requirement coverage rules."""
    return [
        {
            "rule_id": rule.rule_id,
            "label": rule.label,
            "status": "pending",
            "requirement_terms": _matched_terms(rule.requirement_terms, requirement_text),
            "required_evidence_terms": list(rule.evidence_terms),
        }
        for rule in infer_coverage_rules(requirement_text)
    ]


def _rule_is_required(rule: CoverageRule, normalized_requirement: str) -> bool:
    """Return whether a coverage rule is truly required by the requirement."""
    if rule.rule_id != "filter":
        return any(term.lower() in normalized_requirement for term in rule.requirement_terms)
    return _has_filter_interaction_requirement(normalized_requirement)


def _has_filter_interaction_requirement(normalized_requirement: str) -> bool:
    """Avoid treating input sanitization wording as a filter UI requirement."""
    sanitization_terms = ("换行符", "字符", "输入", "sanitize", "sanitise", "newline", "trim")
    for raw_line in normalized_requirement.replace("\r\n", "\n").replace("\r", "\n").split("\n"):
        line = " ".join(raw_line.strip().split())
        if not line:
            continue
        if not any(
            term.lower() in line
            for term in ("\u7b5b\u9009", "\u8fc7\u6ee4", "\u641c\u7d22", "\u5173\u952e\u8bcd", "filter", "search", "query")
        ):
            continue
        if any(term in line for term in sanitization_terms):
            continue
        return True
    return False


def _matched_terms(terms: tuple[str, ...], text: str) -> list[str]:
    """Return terms that are present in text, preserving configured order."""
    normalized = text.lower()
    return [term for term in terms if term.lower() in normalized]


__all__ = [
    "CoverageResult",
    "CoverageRule",
    "CoverageTraceItem",
    "build_testing_checklist",
    "evaluate_requirement_coverage",
    "infer_coverage_rules",
]
