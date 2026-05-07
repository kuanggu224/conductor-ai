"""Scope contract checks derived from frozen requirements."""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from conductor.domain.models import Artifact


NEGATION_TERMS = (
    "不",
    "无",
    "无需",
    "不接",
    "不使用",
    "不实现",
    "不支持",
    "不提供",
    "不得",
    "禁止",
    "仅",
    "只",
    "no ",
    "not ",
    "without",
    "out of scope",
    "non-goal",
)


@dataclass(frozen=True, slots=True)
class ScopeRule:
    """One hard scope rule inferred from a frozen requirement."""

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
        requirement_terms=("登录", "注册", "账户", "认证", "权限", "login", "auth", "account"),
        positive_terms=("登录", "注册", "账户", "认证", "权限", "token", "login", "auth", "account"),
    ),
    ScopeRule(
        rule_id="no_backend",
        label="no backend/api",
        requirement_terms=("后端", "接口", "api", "服务器", "服务端", "server", "backend"),
        positive_terms=("后端", "后端接口", "外部接口", "api", "服务器", "服务端", "endpoint", "route", "fastapi", "flask", "express", "server", "backend"),
    ),
    ScopeRule(
        rule_id="no_database",
        label="no database",
        requirement_terms=("数据库", "db", "database", "mysql", "postgres", "sqlite", "mongodb"),
        positive_terms=("数据库", "db", "database", "mysql", "postgres", "sqlite", "mongodb", "sqlalchemy", "schema", "migration"),
    ),
    ScopeRule(
        rule_id="no_cloud_sync",
        label="no cloud/sync",
        requirement_terms=("云", "同步", "备份", "多设备", "cloud", "sync", "backup"),
        positive_terms=("云", "同步", "备份", "多设备", "上传到云", "cloud", "sync", "backup", "remote"),
    ),
    ScopeRule(
        rule_id="no_import",
        label="no import",
        requirement_terms=("导入", "import"),
        positive_terms=("导入", "上传 csv", "上传文件", "读取上传", "import", "upload csv", "file upload"),
    ),
    ScopeRule(
        rule_id="no_upload",
        label="no upload",
        requirement_terms=("上传", "图片", "封面", "upload", "image", "cover"),
        positive_terms=("上传", "图片上传", "封面上传", "文件上传", "upload", "file input", "image upload"),
    ),
)


def evaluate_scope_contract(frozen_requirement: Artifact | str | None, candidate_content: str) -> ScopeContractResult:
    """Validate candidate content against hard exclusions in a frozen requirement."""
    if frozen_requirement is None:
        return ScopeContractResult()
    requirement_text = frozen_requirement.content if isinstance(frozen_requirement, Artifact) else frozen_requirement
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
            if _contains_any(line, rule.requirement_terms) and _contains_any(line, NEGATION_TERMS):
                rules.append(rule)
                break
    return rules


def _find_positive_evidence(candidate_content: str, positive_terms: tuple[str, ...]) -> str:
    for line in _iter_relevant_lines(candidate_content):
        if not _contains_any(line, positive_terms):
            continue
        if _contains_any(line, NEGATION_TERMS):
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
    metadata_prefixes = (
        "- artifact id:",
        "- project id:",
        "- workitem id:",
        "- agent id:",
        "- kind:",
        "- source backend:",
        "- parent artifact id:",
        "- review of:",
        "- version:",
        "- collaboration session id:",
        "- derived from:",
        "source_backend:",
        "execution_backend:",
    )
    return line.startswith(metadata_prefixes)


def _contains_any(text: str, terms: tuple[str, ...]) -> bool:
    return any(_contains_term(text, term) for term in terms)


def _contains_term(text: str, term: str) -> bool:
    normalized = term.lower()
    if normalized.isascii() and any(char.isalnum() for char in normalized):
        return re.search(rf"(?<![a-z0-9_]){re.escape(normalized)}(?![a-z0-9_])", text) is not None
    return normalized in text


__all__ = [
    "ScopeContractResult",
    "ScopeRule",
    "ScopeViolation",
    "evaluate_scope_contract",
    "infer_scope_rules",
]
