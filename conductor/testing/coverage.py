"""Requirement acceptance coverage checks for test reports."""

from __future__ import annotations

import re
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
    excluded_rules: list[CoverageRule] = field(default_factory=list)
    violated_excluded_rules: list[CoverageRule] = field(default_factory=list)
    traceability: list[CoverageTraceItem] = field(default_factory=list)

    @property
    def passed(self) -> bool:
        return not self.missing_rules and not self.violated_excluded_rules

    def render_markdown(self) -> str:
        """Render a compact coverage section for test reports."""
        if not self.required_rules:
            return "## Requirement Coverage\n\n- No frozen requirement coverage rules inferred.\n"

        covered = ", ".join(rule.label for rule in self.required_rules if rule.rule_id in self.covered_rule_ids) or "-"
        missing = ", ".join(rule.label for rule in self.missing_rules) or "-"
        excluded = ", ".join(rule.label for rule in self.excluded_rules) or "-"
        violated = ", ".join(rule.label for rule in self.violated_excluded_rules) or "-"
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
            f"- Excluded: {excluded}\n"
            f"- Excluded Violations: {violated}\n"
            "\n"
            "### Requirement Traceability\n"
            + "\n".join(trace_lines)
            + "\n"
        )

    def summary(self) -> str:
        """Return a compact failure summary."""
        if self.passed:
            return "Requirement coverage passed"
        if self.violated_excluded_rules:
            labels = ", ".join(rule.label for rule in self.violated_excluded_rules)
            return f"Requirement coverage violates excluded scope: {labels}"
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
        evidence_terms=("api client form interaction updated visible state",),
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
            "preserve",
            "after refresh",
            "persist",
        ),
        evidence_terms=(
            "api client reload preserved submitted values",
            "api client storage changed after submit",
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
        evidence_terms=("api client export/download action triggered",),
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
        evidence_terms=("api client filter interaction changed visible results",),
    ),
    CoverageRule(
        rule_id="delete_item",
        label="delete item interaction",
        requirement_terms=(
            "\u5220\u9664",
            "\u79fb\u9664",
            "delete",
            "deleting",
            "remove",
        ),
        evidence_terms=("api client delete interaction removed visible item",),
    ),
    CoverageRule(
        rule_id="file_import",
        label="file import/upload",
        requirement_terms=(
            "\u5bfc\u5165",
            "\u4e0a\u4f20",
            "\u8bfb\u53d6\u6587\u4ef6",
            "\u89e3\u6790\u6587\u4ef6",
            "\u6587\u4ef6\u5904\u7406",
            "import",
            "upload",
            "file import",
            "file upload",
            "parse csv",
            "csv import",
        ),
        evidence_terms=("api client file import processed sample file",),
    ),
    CoverageRule(
        rule_id="api_behavior",
        label="API endpoint behavior",
        requirement_terms=(
            "\u63a5\u53e3",
            "\u540e\u7aef",
            "\u670d\u52a1",
            "api",
            "restful",
            "http",
        ),
        evidence_terms=("api validation exercised endpoint behavior",),
    ),
    CoverageRule(
        rule_id="api_integration",
        label="API backend API integration",
        requirement_terms=(
            "api client",
            "backend rest api",
            "calls the backend",
            "api-backed",
        ),
        evidence_terms=("api backend api integration verified",),
    ),
    CoverageRule(
        rule_id="db_persistence",
        label="database persistence",
        requirement_terms=(
            "sqlite",
            "database",
            "db",
            "sql",
            "persist",
            "persistence",
            "\u6570\u636e\u5e93",
            "\u6301\u4e45\u5316",
        ),
        evidence_terms=("sqlite persistence verified", "database persistence verified"),
    ),
)


EVIDENCE_ALIASES: dict[str, tuple[str, ...]] = {
    "add_item": ("browser form interaction updated visible state",),
    "persistence": (
        "browser reload preserved submitted values",
        "browser localstorage changed after form submit",
    ),
    "export_csv": ("browser export/download action triggered",),
    "filter": ("browser filter interaction changed visible results",),
    "delete_item": ("browser delete interaction removed visible item",),
    "file_import": ("browser file import processed sample file",),
}


def evaluate_requirement_coverage(frozen_requirement: Artifact | str | None, validation_output: str) -> CoverageResult:
    """Evaluate whether validation output covers key frozen requirement terms."""
    if frozen_requirement is None:
        return CoverageResult()

    requirement_text = frozen_requirement.content if isinstance(frozen_requirement, Artifact) else frozen_requirement
    required_rules = infer_coverage_rules(requirement_text)
    excluded_rules = infer_excluded_coverage_rules(requirement_text)
    output = validation_output.lower()
    covered_rule_ids = [
        rule.rule_id
        for rule in required_rules
        if _rule_is_covered(rule, output)
    ]
    missing_rules = [rule for rule in required_rules if rule.rule_id not in set(covered_rule_ids)]
    violated_excluded_rules = [
        rule
        for rule in excluded_rules
        if _rule_is_covered(rule, output) or f"feature present: {rule.label}".lower() in output
    ]
    return CoverageResult(
        required_rules=required_rules,
        covered_rule_ids=covered_rule_ids,
        missing_rules=missing_rules,
        excluded_rules=excluded_rules,
        violated_excluded_rules=violated_excluded_rules,
        traceability=[
            CoverageTraceItem(
                rule_id=rule.rule_id,
                label=rule.label,
                status="covered" if rule.rule_id in covered_rule_ids else "missing",
                requirement_terms=_matched_terms(rule.requirement_terms, requirement_text),
                evidence_terms=_matched_evidence_terms(rule, output),
            )
            for rule in required_rules
        ],
    )


def infer_coverage_rules(requirement_text: str) -> list[CoverageRule]:
    """Infer coverage rules from frozen requirement text."""
    normalized = _included_requirement_text(requirement_text.lower())
    return [
        rule
        for rule in COVERAGE_RULES
        if _rule_is_required(rule, normalized)
    ]


def infer_excluded_coverage_rules(requirement_text: str) -> list[CoverageRule]:
    """Infer validation rules explicitly excluded by non-goals or out-of-scope lines."""
    normalized_lines = _normalized_lines(requirement_text.lower())
    excluded: list[CoverageRule] = []
    for rule in COVERAGE_RULES:
        if any(_line_excludes_rule(line, rule) for line in normalized_lines):
            excluded.append(rule)
    return excluded


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
    if any(excluded.rule_id == rule.rule_id for excluded in infer_excluded_coverage_rules(normalized_requirement)):
        return False
    if rule.rule_id in {"add_item", "persistence", "filter", "delete_item", "file_import", "export_csv"}:
        if _has_api_behavior_requirement(normalized_requirement) and not _has_client_interaction_requirement(normalized_requirement):
            return False
    if rule.rule_id == "export_csv":
        return _has_export_interaction_requirement(normalized_requirement)
    if rule.rule_id == "api_behavior":
        return _has_api_behavior_requirement(normalized_requirement)
    if rule.rule_id == "api_integration":
        return _has_api_integration_requirement(normalized_requirement)
    if rule.rule_id == "db_persistence":
        return _has_database_persistence_requirement(normalized_requirement)
    if rule.rule_id != "filter":
        return any(term.lower() in normalized_requirement for term in rule.requirement_terms)
    return _has_filter_interaction_requirement(normalized_requirement)


def _rule_is_covered(rule: CoverageRule, normalized_output: str) -> bool:
    """Return whether validation output proves the required coverage rule."""
    evidence_terms = (*rule.evidence_terms, *EVIDENCE_ALIASES.get(rule.rule_id, ()))
    if any(term.lower() in normalized_output for term in evidence_terms):
        return True
    if rule.rule_id == "api_behavior":
        return _has_api_endpoint_evidence(normalized_output)
    return False


def _line_excludes_rule(line: str, rule: CoverageRule) -> bool:
    """Return whether a single line makes a feature an explicit non-goal."""
    if rule.rule_id == "db_persistence" and any(
        marker in line
        for marker in (
            "production database",
            "external database",
            "external databases",
        )
    ):
        return False
    broad_exclusion_markers = (
        "out of scope",
        "non-goal",
        "non goal",
        "not in scope",
        "excluded",
        "exclude",
        "\u975e\u76ee\u6807",
        "\u8303\u56f4\u5916",
        "\u4e0d\u5305\u542b",
    )
    local_exclusion_markers = (
        "no ",
        "without ",
        "\u4e0d\u9700\u8981",
        "\u65e0\u9700",
        "\u4e0d\u505a",
        "\u4e0d\u652f\u6301",
    )
    broad_offsets = [line.find(marker) for marker in broad_exclusion_markers if marker in line]
    if broad_offsets:
        scoped_text = line[min(offset for offset in broad_offsets if offset >= 0):]
        return any(_contains_term(scoped_text, term) for term in rule.requirement_terms)
    for term in rule.requirement_terms:
        for match in _term_matches(line, term):
            prefix = line[max(0, match - 24):match]
            if any(marker in prefix for marker in local_exclusion_markers):
                return True
    return False


def _term_matches(text: str, term: str) -> list[int]:
    """Return start offsets for a requirement term."""
    normalized_term = term.lower()
    if normalized_term.isascii() and re.search(r"[a-z0-9]", normalized_term):
        return [match.start() for match in re.finditer(rf"(?<![a-z0-9_]){re.escape(normalized_term)}(?![a-z0-9_])", text)]
    matches: list[int] = []
    start = 0
    while True:
        index = text.find(normalized_term, start)
        if index < 0:
            break
        matches.append(index)
        start = index + max(len(normalized_term), 1)
    return matches


def _has_export_interaction_requirement(normalized_requirement: str) -> bool:
    """Avoid treating CSV import or parsing requirements as export/download needs."""
    return any(
        term in normalized_requirement
        for term in ("\u5bfc\u51fa", "\u4e0b\u8f7d", "export", "download")
    )


def _has_client_interaction_requirement(normalized_requirement: str) -> bool:
    """Return whether API-adjacent wording also asks for client-facing interaction evidence."""
    client_terms = (
        "\u9875\u9762",
        "\u8868\u5355",
        "\u754c\u9762",
        "page",
        "form",
        "ui",
        "web app",
        "api client",
        "frontend",
        "visible",
        "button",
    )
    return any(_contains_term(normalized_requirement, term) for term in client_terms)


def _has_api_behavior_requirement(normalized_requirement: str) -> bool:
    """Return whether the requirement asks for backend/API behavior."""
    api_terms = ("\u63a5\u53e3", "\u540e\u7aef", "\u670d\u52a1", "api", "restful", "http")
    negation_terms = (
        "\u4e0d\u63a5 api",
        "\u4e0d\u63a5api",
        "\u65e0 api",
        "\u65e0api",
        "\u65e0\u540e\u7aef",
        "\u4e0d\u9700\u8981\u540e\u7aef",
        "\u4e0d\u63a5\u540e\u7aef",
        "no api",
        "without api",
        "no backend",
        "without backend",
        "out of scope",
        "non-goal",
        "non goal",
        "excluded",
        "browser api",
        "localstorage api",
    )
    for raw_line in normalized_requirement.replace("\r\n", "\n").replace("\r", "\n").split("\n"):
        line = " ".join(raw_line.strip().split())
        if not line or not any(term in line for term in api_terms):
            continue
        if any(term in line for term in negation_terms):
            continue
        return True
    return False


def _has_api_integration_requirement(normalized_requirement: str) -> bool:
    """Return whether client/API wording requires end-to-end integration evidence."""
    return _has_api_behavior_requirement(normalized_requirement) and _has_client_interaction_requirement(normalized_requirement)


def _has_database_persistence_requirement(normalized_requirement: str) -> bool:
    """Return whether the requirement asks for durable database-backed behavior."""
    database_terms = ("sqlite", "database", "db", "sql", "\u6570\u636e\u5e93")
    persistence_terms = ("persist", "persistence", "persistent", "stored", "durable", "\u6301\u4e45\u5316", "\u4fdd\u5b58")
    negation_terms = (
        "no database",
        "without database",
        "no db",
        "without db",
        "in-memory",
        "in memory",
        "\u65e0\u6570\u636e\u5e93",
        "\u4e0d\u9700\u8981\u6570\u636e\u5e93",
        "\u4e0d\u63a5\u6570\u636e\u5e93",
    )
    for line in _normalized_lines(normalized_requirement):
        has_database = any(_contains_term(line, term) for term in database_terms)
        has_persistence = any(_contains_term(line, term) for term in persistence_terms)
        if not (has_database or (has_persistence and _has_api_behavior_requirement(normalized_requirement))):
            continue
        if any(_contains_term(line, term) for term in negation_terms) or _line_negates_database_scope(line):
            continue
        return True
    return False


def _line_negates_database_scope(line: str) -> bool:
    """Return whether a line mentions database only as excluded scope."""
    scoped_negation_markers = (
        "out of scope",
        "non-goal",
        "non goal",
        "not in scope",
        "excluded",
        "exclude",
        "no production database",
        "no external database",
        "without database",
        "\u975e\u76ee\u6807",
        "\u8303\u56f4\u5916",
        "\u4e0d\u5305\u542b",
        "\u4e0d\u9700\u8981",
        "\u65e0\u9700",
        "\u4e0d\u505a",
    )
    database_terms = ("database", "databases", "db", "sqlite", "\u6570\u636e\u5e93")
    return any(_contains_term(line, marker) for marker in scoped_negation_markers) and any(
        _contains_term(line, term) for term in database_terms
    )


def _has_api_endpoint_evidence(normalized_output: str) -> bool:
    """Return whether validation output proves API behavior, not just test success."""
    endpoint_signal = any(
        term in normalized_output
        for term in (
            "/api",
            "endpoint",
            "route",
            "testclient",
            "httpx",
            "requests.",
            "fastapi",
            "flask",
            "django",
        )
    )
    method_signal = re.search(r"\b(get|post|put|patch|delete|options|head)\s+[/\w-]", normalized_output) is not None
    status_signal = (
        re.search(r"\bstatus(?:_code| code)?\s*[:=]?\s*[1-5]\d\d\b", normalized_output) is not None
        or re.search(r"\bhttp\s+[1-5]\d\d\b", normalized_output) is not None
        or re.search(r"->\s*[1-5]\d\d\b", normalized_output) is not None
    )
    response_signal = any(term in normalized_output for term in ("response", "payload", "json body", "json=", "body="))
    return (endpoint_signal or method_signal) and (status_signal or response_signal)


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


def _normalized_lines(normalized_text: str) -> list[str]:
    """Return compact lower-cased requirement lines."""
    return [
        " ".join(raw_line.strip().split())
        for raw_line in normalized_text.replace("\r\n", "\n").replace("\r", "\n").split("\n")
        if raw_line.strip()
    ]


def _included_requirement_text(normalized_text: str) -> str:
    """Drop explicit out-of-scope segments before requirement term matching."""
    exclusion_markers = (
        "out of scope",
        "non-goal",
        "non goal",
        "not in scope",
        "excluded",
        "exclude",
        "\u975e\u76ee\u6807",
        "\u8303\u56f4\u5916",
        "\u4e0d\u5305\u542b",
    )
    included_lines: list[str] = []
    for line in _normalized_lines(normalized_text):
        offsets = [line.find(marker) for marker in exclusion_markers if marker in line]
        if offsets:
            line = line[: min(offset for offset in offsets if offset >= 0)].strip(" .;,\uff0c\u3002")
        if line:
            included_lines.append(line)
    return "\n".join(included_lines)


def _contains_term(text: str, term: str) -> bool:
    """Match ASCII words by boundary while keeping phrase/CJK substring matching."""
    normalized_term = term.lower()
    if normalized_term.isascii() and re.search(r"[a-z0-9]", normalized_term):
        return re.search(rf"(?<![a-z0-9_]){re.escape(normalized_term)}(?![a-z0-9_])", text) is not None
    return normalized_term in text


def _matched_terms(terms: tuple[str, ...], text: str) -> list[str]:
    """Return terms that are present in text, preserving configured order."""
    normalized = text.lower()
    return [term for term in terms if term.lower() in normalized]


def _matched_evidence_terms(rule: CoverageRule, output: str) -> list[str]:
    """Return canonical evidence terms, including when legacy aliases matched."""
    canonical_matches = _matched_terms(rule.evidence_terms, output)
    if canonical_matches:
        return canonical_matches
    if _matched_terms(EVIDENCE_ALIASES.get(rule.rule_id, ()), output):
        return [rule.evidence_terms[0]]
    return []


__all__ = [
    "CoverageResult",
    "CoverageRule",
    "CoverageTraceItem",
    "build_testing_checklist",
    "evaluate_requirement_coverage",
    "infer_excluded_coverage_rules",
    "infer_coverage_rules",
]
