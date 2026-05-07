"""Artifact output contracts.

The contract is intentionally lightweight: it validates whether an artifact
contains the minimum sections downstream agents need for context building.
"""

from __future__ import annotations

from dataclasses import dataclass

from conductor.domain.models import Artifact


DEFAULT_REQUIRED_SECTIONS: dict[str, list[str]] = {
    "design_overview": ["目标", "范围", "方案", "验收"],
    "requirement_spec": ["目标", "范围", "验收", "风险"],
    "frozen_requirement_spec": ["目标", "范围", "验收", "风险"],
    "ui_design": ["页面", "交互", "验收"],
    "api_design": ["接口", "输入", "输出", "验收"],
    "test_design": ["测试", "通过标准"],
    "api_implementation": ["实现", "接口", "运行"],
    "ui_implementation": ["实现", "页面", "运行"],
    "data_implementation": ["数据", "结构", "运行"],
    "generic_implementation": ["实现", "运行"],
    "acceptance_check": ["结论", "风险"],
    "automated_test": ["命令", "结果"],
    "api_validation": ["接口", "结论"],
    "ui_validation": ["页面", "结论"],
    "collaboration_review": ["审阅", "结论"],
}


@dataclass(slots=True)
class ArtifactContractResult:
    """Validation result for one artifact."""

    kind: str
    required_sections: list[str]
    missing_sections: list[str]

    @property
    def passed(self) -> bool:
        return not self.missing_sections


def validate_artifact_contract(artifact: Artifact) -> ArtifactContractResult:
    """Validate an artifact against its lightweight output contract."""
    required_sections = DEFAULT_REQUIRED_SECTIONS.get(artifact.kind, [])
    content = artifact.content.lower()
    missing_sections = [
        section for section in required_sections if section.lower() not in content
    ]
    return ArtifactContractResult(
        kind=artifact.kind,
        required_sections=required_sections,
        missing_sections=missing_sections,
    )


def build_contract_markdown(artifact: Artifact) -> str:
    """Build a small Markdown block describing contract quality."""
    result = validate_artifact_contract(artifact)
    status = "pass" if result.passed else "missing_sections"
    required = ", ".join(result.required_sections) if result.required_sections else "-"
    missing = ", ".join(result.missing_sections) if result.missing_sections else "-"
    return (
        "## Artifact Contract\n\n"
        f"- Status: `{status}`\n"
        f"- Required Sections: `{required}`\n"
        f"- Missing Sections: `{missing}`\n\n"
    )
