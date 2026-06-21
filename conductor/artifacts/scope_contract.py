"""Scope contract checks derived from frozen requirements."""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from conductor.domain.models import Artifact


NEGATION_TERMS = (
    "no ",
    "not ",
    "without",
    "excluded",
    "out of scope",
    "non-goal",
    "non-goals",
    "\u4e0d",
    "\u65e0",
    "\u65e0\u9700",
    "\u4e0d\u63a5",
    "\u4e0d\u4f7f\u7528",
    "\u4e0d\u5b9e\u73b0",
    "\u4e0d\u652f\u6301",
    "\u4e0d\u63d0\u4f9b",
    "\u4e0d\u5f97",
    "\u7981\u6b62",
    "\u4ec5",
    "\u53ea",
)


@dataclass(frozen=True, slots=True)
class ScopeRule:
    """One hard scope rule inferred from frozen requirement text."""

    rule_id: str
    label: str
    requirement_terms: tuple[str, ...]
    positive_terms: tuple[str, ...]


@dataclass(slots=True)
class ScopeViolation:
    """One candidate scope violation."""

    rule_id: str
    label: str
    evidence: str


@dataclass(slots=True)
class ScopeContractResult:
    """Scope contract validation result."""

    rules: list[ScopeRule] = field(default_factory=list)
    violations: list[ScopeViolation] = field(default_factory=list)

    @property
    def passed(self) -> bool:
        return not self.violations

    def summary(self) -> str:
        """Return a compact human-readable summary."""
        if not self.violations:
            return "Scope contract passed"
        labels = ", ".join(f"{item.label}: {item.evidence}" for item in self.violations[:3])
        return f"Scope contract violation: {labels}"


SCOPE_RULES: tuple[ScopeRule, ...] = (
    ScopeRule(
        rule_id="no_login",
        label="no login/auth",
        requirement_terms=("\u767b\u5f55", "\u6ce8\u518c", "\u8d26\u6237", "\u8ba4\u8bc1", "\u6743\u9650", "login", "auth", "account"),
        positive_terms=("\u767b\u5f55", "\u6ce8\u518c", "\u8d26\u6237", "\u8ba4\u8bc1", "\u6743\u9650", "token", "login", "auth", "account"),
    ),
    ScopeRule(
        rule_id="no_backend",
        label="no backend/api",
        requirement_terms=("\u540e\u7aef", "\u63a5\u53e3", "api", "\u670d\u52a1\u5668", "\u670d\u52a1\u7aef", "server", "backend"),
        positive_terms=("\u540e\u7aef", "\u540e\u7aef\u63a5\u53e3", "\u5916\u90e8\u63a5\u53e3", "api", "\u670d\u52a1\u5668", "\u670d\u52a1\u7aef", "endpoint", "route", "fastapi", "flask", "express", "server", "backend"),
    ),
    ScopeRule(
        rule_id="no_database",
        label="no database",
        requirement_terms=("\u6570\u636e\u5e93", "db", "database", "mysql", "postgres", "sqlite", "mongodb"),
        positive_terms=("\u6570\u636e\u5e93", "db", "database", "mysql", "postgres", "sqlite", "mongodb", "sqlalchemy", "schema", "migration"),
    ),
    ScopeRule(
        rule_id="no_cloud_sync",
        label="no cloud/sync",
        requirement_terms=("\u4e91", "\u540c\u6b65", "\u5907\u4efd", "\u591a\u8bbe\u5907", "cloud", "sync", "backup"),
        positive_terms=("\u4e91", "\u4e91\u540c\u6b65", "\u8fdc\u7a0b\u540c\u6b65", "\u8de8\u8bbe\u5907\u540c\u6b65", "\u591a\u8bbe\u5907\u540c\u6b65", "\u5907\u4efd", "\u591a\u8bbe\u5907", "\u4e0a\u4f20\u5230\u4e91", "cloud", "cloud sync", "remote sync", "cross-device sync", "multi-device sync", "backup", "remote"),
    ),
    ScopeRule(
        rule_id="no_import",
        label="no import",
        requirement_terms=("\u5bfc\u5165", "import"),
        positive_terms=("\u5bfc\u5165", "\u4e0a\u4f20 csv", "\u4e0a\u4f20\u6587\u4ef6", "\u8bfb\u53d6\u4e0a\u4f20", "import", "upload csv", "file upload"),
    ),
    ScopeRule(
        rule_id="no_upload",
        label="no upload",
        requirement_terms=("\u4e0a\u4f20", "\u56fe\u7247", "\u5c01\u9762", "upload", "image", "cover"),
        positive_terms=("\u4e0a\u4f20", "\u56fe\u7247\u4e0a\u4f20", "\u5c01\u9762\u4e0a\u4f20", "\u6587\u4ef6\u4e0a\u4f20", "upload", "file input", "image upload"),
    ),
)


def evaluate_scope_contract(frozen_requirement: Artifact | str | None, candidate_content: str) -> ScopeContractResult:
    """Validate candidate content against hard exclusions in a frozen requirement."""
    if frozen_requirement is None:
        return ScopeContractResult()
    requirement_text = frozen_requirement.content if isinstance(frozen_requirement, Artifact) else str(frozen_requirement)
    rules = infer_scope_rules(requirement_text)
    violations: list[ScopeViolation] = []
    for rule in rules:
        evidence = _find_positive_evidence(candidate_content, rule.positive_terms)
        if evidence:
            violations.append(ScopeViolation(rule_id=rule.rule_id, label=rule.label, evidence=evidence))
    return ScopeContractResult(rules=rules, violations=violations)


def infer_scope_rules(requirement_text: str) -> list[ScopeRule]:
    """Infer hard scope rules from explicit non-goal/negative requirement language."""
    rules: list[ScopeRule] = []
    for rule in SCOPE_RULES:
        for line in _iter_relevant_lines(requirement_text):
            if _line_excludes_rule(line, rule):
                rules.append(rule)
                break
    return rules


def _line_excludes_rule(line: str, rule: ScopeRule) -> bool:
    """Return whether a negative scope phrase applies to this rule."""
    if rule.rule_id == "no_database" and _contains_any(
        line,
        (
            "no production database",
            "no external database",
            "no external databases",
            "production database",
            "external database",
            "external databases",
            "without production database",
            "without external database",
        ),
    ):
        return False
    broad_markers = (
        "out of scope",
        "non-goal",
        "non-goals",
        "non goal",
        "not in scope",
        "excluded",
        "exclude",
        "\u975e\u76ee\u6807",
        "\u8303\u56f4\u5916",
        "\u4e0d\u5305\u542b",
    )
    offsets = [line.find(marker) for marker in broad_markers if marker in line]
    if offsets:
        scoped_text = line[min(offset for offset in offsets if offset >= 0):]
        return _contains_any(scoped_text, rule.requirement_terms)
    local_markers = (
        "no ",
        "not ",
        "without",
        "must not",
        "do not",
        "should not",
        "\u4e0d",
        "\u65e0",
        "\u65e0\u9700",
        "\u4e0d\u63a5",
        "\u4e0d\u4f7f\u7528",
        "\u4e0d\u5b9e\u73b0",
        "\u4e0d\u652f\u6301",
        "\u4e0d\u63d0\u4f9b",
        "\u4e0d\u5f97",
        "\u7981\u6b62",
    )
    for term in rule.requirement_terms:
        for match in _term_matches(line, term):
            prefix = line[max(0, match - 32):match]
            if any(marker in prefix for marker in local_markers):
                return True
    return False


def _find_positive_evidence(candidate_content: str, positive_terms: tuple[str, ...]) -> str:
    for line in _iter_relevant_lines(candidate_content):
        if not _contains_any(line, positive_terms):
            continue
        if _contains_any(line, NEGATION_TERMS) or _is_negative_scope_statement(line):
            continue
        if _is_mock_or_runtime_note(line):
            continue
        return line[:220]
    return ""


def _iter_relevant_lines(text: str) -> list[str]:
    lines: list[str] = []
    for raw_line in text.replace("\r\n", "\n").replace("\r", "\n").split("\n"):
        line = " ".join(raw_line.strip().split())
        if not line:
            continue
        if _is_artifact_metadata_line(line.lower()):
            continue
        lines.append(line.lower())
    return lines


def _is_artifact_metadata_line(line: str) -> bool:
    line = line.lstrip("-* ").strip("` ")
    metadata_prefixes = (
        "workitem id:",
        "agent:",
        "kind:",
        "source:",
        "source backend:",
        "run profile:",
        "validation command:",
        "derived from:",
        "collaboration session id:",
    )
    return line.startswith(metadata_prefixes)


def _is_mock_or_runtime_note(line: str) -> bool:
    noise_terms = (
        "mock fallback",
        "mock artifact",
        "real backend execution",
        "source_backend",
        "scope contract",
        "validation command",
        "cli stdout",
        "cli stderr",
    )
    return _contains_any(line, noise_terms)


def _is_negative_scope_statement(line: str) -> bool:
    negative_markers = (
        "out of scope",
        "non-goal",
        "non goal",
        "excluded",
        "exclude",
        "must not",
        "do not",
        "should not",
        "without",
        "avoid",
        "continue to avoid",
        "\u975e\u76ee\u6807",
        "\u8303\u56f4\u5916",
        "\u4e0d\u5305\u542b",
        "\u4e0d\u9700\u8981",
        "\u65e0\u9700",
        "\u4e0d\u505a",
    )
    return _contains_any(line, negative_markers)


def _contains_any(text: str, terms: tuple[str, ...]) -> bool:
    return any(_contains_term(text, term) for term in terms)


def _term_matches(text: str, term: str) -> list[int]:
    term = term.lower()
    if term.isascii() and term.replace("_", "").replace("-", "").isalnum():
        return [match.start() for match in re.finditer(rf"(?<![a-z0-9_]){re.escape(term)}(?![a-z0-9_])", text)]
    matches: list[int] = []
    start = 0
    while True:
        index = text.find(term, start)
        if index < 0:
            break
        matches.append(index)
        start = index + max(len(term), 1)
    return matches


def _contains_term(text: str, term: str) -> bool:
    term = term.lower()
    if term.isascii() and term.replace("_", "").replace("-", "").isalnum():
        return re.search(rf"(?<![a-z0-9_]){re.escape(term)}(?![a-z0-9_])", text) is not None
    return term in text


__all__ = [
    "ScopeContractResult",
    "ScopeRule",
    "ScopeViolation",
    "evaluate_scope_contract",
    "infer_scope_rules",
]
