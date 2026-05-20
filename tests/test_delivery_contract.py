"""Delivery contract tests."""

from conductor.delivery_contract import (
    build_acceptance_trace,
    build_delivery_contract,
    render_acceptance_trace_markdown,
    render_delivery_contract_markdown,
)


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


def test_delivery_contract_marks_frozen_design_as_downstream_baseline() -> None:
    contract = build_delivery_contract(
        stage="development",
        kind="ui_implementation",
        role="frontend_engineer",
        required_input_artifact_ids=["artifact-frozen-req", "artifact-frozen-design"],
        required_input_kinds=["frozen_requirement_spec", "frozen_design_spec"],
    )

    assert "frozen_design_spec" in contract["required_input_kinds"]
    assert "Treat the frozen design as the controlling implementation and testing baseline." in contract["guardrails"]
    assert "Frozen design coverage" in contract["verification_focus"]


def test_acceptance_trace_records_validation_and_changed_file_evidence() -> None:
    trace = build_acceptance_trace(
        ["UI can add a book", "Data persists after refresh"],
        validation_success=True,
        changed_files=["index.html", "static/app.js"],
    )

    assert [item["status"] for item in trace] == ["passed", "passed"]
    assert "index.html, static/app.js" in trace[0]["evidence"]

    markdown = "\n".join(render_acceptance_trace_markdown(trace))

    assert "[passed] UI can add a book" in markdown
    assert "Post-edit validation passed" in markdown
