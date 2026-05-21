"""Command templates for Task Center handoff payloads."""

from __future__ import annotations

from conductor.domain.models import TaskAssignment, TaskAssignmentStatus


def build_task_return_commands(project_root: str, assignment: TaskAssignment) -> dict[str, str]:
    """Return copyable CLI commands for an actively claimed assignment."""
    if assignment.status != TaskAssignmentStatus.CLAIMED:
        return {}
    root = project_root or "<project-root>"
    agent_id = assignment.assigned_agent_id or "<agent-id>"
    claim_token = assignment.claim_token or "<claim-token>"
    base = ["python", "-m", "app.task_center"]
    common = [
        _quote_cli_arg(assignment.id),
        "--project-root",
        _quote_cli_arg(root),
        "--agent-id",
        _quote_cli_arg(agent_id),
        "--claim-token",
        _quote_cli_arg(claim_token),
    ]
    complete = [*base, "complete", *common, "--result-summary", _quote_cli_arg("done")]
    fail = [*base, "fail", *common, "--blocked-reason", _quote_cli_arg("blocked")]
    return {
        "complete": " ".join(complete),
        "complete_with_output_file": " ".join([*complete, "--output-file", _quote_cli_arg("result.md")]),
        "fail": " ".join(fail),
        "fail_with_output_file": " ".join([*fail, "--output-file", _quote_cli_arg("result.md")]),
        "heartbeat": " ".join([*base, "heartbeat", *common]),
        "release": " ".join([*base, "release", *common, "--release-reason", _quote_cli_arg("release claim")]),
    }


def _quote_cli_arg(value: object) -> str:
    return '"' + str(value).replace('"', '\\"') + '"'


__all__ = ["build_task_return_commands"]
