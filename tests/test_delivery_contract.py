"""Delivery contract tests."""

from conductor.delivery_contract import build_delivery_contract, render_delivery_contract_markdown


def test_delivery_contract_dedupes_inputs_and_marks_rework_focus() -> None:
    contract = build_delivery_contract(
        stage="development",
        kind="ui_implementation",
        role="frontend_engineer",
        required_input_artifact_ids=["artifact-frozen", "artifact-frozen", "artifact-design"],
        required_input_kinds=["frozen_requirement_spec", "frozen_requirement_spec", "ui_design"],
        is_rework=True,
    )

    assert contract["required_input_artifact_ids"] == ["artifact-frozen", "artifact-design"]
    assert contract["required_input_kinds"] == ["frozen_requirement_spec", "ui_design"]
    assert "Fix only the referenced feedback" in contract["guardrails"][1]
    assert "Visible UI state and interaction behavior" in contract["verification_focus"]
    assert "Referenced testing feedback is directly addressed" in contract["verification_focus"]

    markdown = "\n".join(render_delivery_contract_markdown(contract))

    assert "Required Input Artifacts: artifact-frozen, artifact-design" in markdown
    assert "Expected Outputs" in markdown
    assert "Guardrails" in markdown
