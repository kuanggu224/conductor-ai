"""Shared delivery contract for Agent task execution."""

from __future__ import annotations


def build_delivery_contract(
    *,
    stage: str,
    kind: str,
    role: str,
    required_input_artifact_ids: list[str] | None = None,
    required_input_kinds: list[str] | None = None,
    is_rework: bool = False,
) -> dict[str, object]:
    """Return the explicit stage-level contract an Agent must satisfy."""
    guardrails = [
        "Treat the frozen requirement as the controlling scope contract.",
        "Cite the input artifact ids that shaped the result.",
        "Do not add unrelated features, files, dependencies, or architecture.",
        "If the task is blocked, return failure with a precise blocked_reason instead of inventing output.",
    ]
    if is_rework:
        guardrails.insert(1, "Fix only the referenced feedback and preserve existing accepted behavior.")
    return {
        "stage": stage,
        "kind": kind,
        "role": role,
        "required_input_artifact_ids": list(dict.fromkeys(required_input_artifact_ids or [])),
        "required_input_kinds": list(dict.fromkeys(required_input_kinds or [])),
        "expected_outputs": expected_outputs_for(stage, kind),
        "guardrails": guardrails,
        "verification_focus": verification_focus_for(stage, kind, is_rework=is_rework),
    }


def expected_outputs_for(stage: str, kind: str) -> list[str]:
    """Return expected output obligations for one stage/kind pair."""
    if stage == "requirement":
        return [
            "A structured requirement specification with goals, scope, non-goals, acceptance criteria, risks, and open questions.",
            "A clear downstream baseline that design, development, and testing can execute without guessing.",
        ]
    if stage == "design":
        return [
            "A design document that maps the frozen requirement into implementation boundaries.",
            "Explicit downstream constraints for development and testing agents.",
        ]
    if stage == "development":
        outputs = [
            "Concrete implementation changes or a precise implementation artifact if code execution is not enabled.",
            "Changed-file summary and verification notes tied to acceptance criteria.",
        ]
        if kind == "ui_implementation":
            outputs.append("UI behavior evidence for core interactions and visible states.")
        if kind == "api_implementation":
            outputs.append("API contract notes covering inputs, outputs, and error cases.")
        return outputs
    if stage == "testing":
        return [
            "A validation report with commands/checks run, pass/fail status, and evidence.",
            "Precise missing coverage or failure feedback that can drive a development rework task.",
        ]
    return ["A concise artifact that satisfies the WorkItem acceptance criteria."]


def verification_focus_for(stage: str, kind: str, *, is_rework: bool = False) -> list[str]:
    """Return verification focus points for one stage/kind pair."""
    focus = ["WorkItem acceptance criteria", "Frozen requirement coverage"]
    if stage == "development":
        focus.extend(["Scope boundary preservation", "Runnable or inspectable implementation output"])
    if stage == "testing":
        focus.extend(["Executable validation evidence", "Actionable failure feedback"])
    if kind == "ui_implementation":
        focus.append("Visible UI state and interaction behavior")
    if kind == "api_implementation":
        focus.append("API input/output contract behavior")
    if is_rework:
        focus.append("Referenced testing feedback is directly addressed")
    return list(dict.fromkeys(focus))


def render_delivery_contract_markdown(contract: dict[str, object]) -> list[str]:
    """Render a delivery contract as compact Markdown bullet lines."""
    if not contract:
        return ["- None"]
    return [
        f"- Stage: {contract.get('stage', '')}",
        f"- Kind: {contract.get('kind', '')}",
        f"- Role: {contract.get('role', '')}",
        f"- Required Input Artifacts: {_join_or_none(_list_payload(contract.get('required_input_artifact_ids')))}",
        f"- Required Input Kinds: {_join_or_none(_list_payload(contract.get('required_input_kinds')))}",
        "- Expected Outputs:",
        *_bullet_lines(_list_payload(contract.get("expected_outputs"))),
        "- Guardrails:",
        *_bullet_lines(_list_payload(contract.get("guardrails"))),
        "- Verification Focus:",
        *_bullet_lines(_list_payload(contract.get("verification_focus"))),
    ]


def _list_payload(value: object) -> list[object]:
    return value if isinstance(value, list) else []


def _bullet_lines(items: list[object]) -> list[str]:
    return [f"- {item}" for item in items] if items else ["- Not specified"]


def _join_or_none(items: list[object]) -> str:
    return ", ".join(str(item) for item in items) if items else "None"


__all__ = [
    "build_delivery_contract",
    "expected_outputs_for",
    "render_delivery_contract_markdown",
    "verification_focus_for",
]
