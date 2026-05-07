"""Task Center CLI for persisted Conductor project states."""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict, replace
from pathlib import Path

from conductor.domain.models import SharedProjectState, TaskAssignment, TaskAssignmentStatus, WorkItemStatus
from conductor.io.encoding import configure_utf8_stdio
from conductor.state.file_store import FileStateStore

configure_utf8_stdio()


def build_parser() -> argparse.ArgumentParser:
    """Build the task-center command parser."""
    parser = argparse.ArgumentParser(prog="python -m app.task_center")
    subparsers = parser.add_subparsers(dest="command", required=True)

    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--project-root", default=str(Path.cwd()), help="Project root containing .conductor/state.")
    common.add_argument("--state-dir", help="Explicit state directory. Overrides --project-root.")
    common.add_argument("--project-id", help="Project id. Optional when the state directory contains one project.")

    list_parser = subparsers.add_parser("list", parents=[common], help="List task-center assignments.")
    list_parser.add_argument("--status", choices=[status.value for status in TaskAssignmentStatus])

    claim_parser = subparsers.add_parser("claim", parents=[common], help="Claim one queued assignment.")
    claim_parser.add_argument("assignment_id")
    claim_parser.add_argument("--agent-id", required=True)
    claim_parser.add_argument("--claim-reason", default="")

    claim_next_parser = subparsers.add_parser("claim-next", parents=[common], help="Claim the next queued assignment.")
    claim_next_parser.add_argument("--agent-id", required=True)
    claim_next_parser.add_argument("--role", help="Only claim assignments for this role.")
    claim_next_parser.add_argument("--claim-reason", default="")

    complete_parser = subparsers.add_parser("complete", parents=[common], help="Return one claimed assignment as completed.")
    complete_parser.add_argument("assignment_id")
    complete_parser.add_argument("--result-summary", default="")
    complete_parser.add_argument("--output-artifact-id", action="append", default=[])

    fail_parser = subparsers.add_parser("fail", parents=[common], help="Return one claimed assignment as failed.")
    fail_parser.add_argument("assignment_id")
    fail_parser.add_argument("--result-summary", default="")
    fail_parser.add_argument("--blocked-reason", default="")
    fail_parser.add_argument("--output-artifact-id", action="append", default=[])
    return parser


def main(argv: list[str] | None = None) -> int:
    """Run the task-center command and print a JSON payload."""
    args = build_parser().parse_args(argv)
    store = FileStateStore(_resolve_state_dir(args))
    state = _resolve_state(store, args.project_id)

    try:
        if args.command == "list":
            payload = _list_payload(state, status=args.status)
        elif args.command == "claim":
            state, assignment = _claim_assignment(
                store,
                state,
                assignment_id=args.assignment_id,
                agent_id=args.agent_id,
                claim_reason=args.claim_reason,
            )
            payload = _assignment_payload(state, assignment)
        elif args.command == "claim-next":
            assignment = _select_next_assignment(state, role=args.role)
            state, assignment = _claim_assignment(
                store,
                state,
                assignment_id=assignment.id,
                agent_id=args.agent_id,
                claim_reason=args.claim_reason,
            )
            payload = _assignment_payload(state, assignment)
        elif args.command == "complete":
            state, assignment = _return_assignment(
                store,
                state,
                assignment_id=args.assignment_id,
                status=TaskAssignmentStatus.COMPLETED,
                result_summary=args.result_summary,
                output_artifact_ids=args.output_artifact_id,
            )
            payload = _assignment_payload(state, assignment)
        elif args.command == "fail":
            state, assignment = _return_assignment(
                store,
                state,
                assignment_id=args.assignment_id,
                status=TaskAssignmentStatus.FAILED,
                result_summary=args.result_summary,
                output_artifact_ids=args.output_artifact_id,
                blocked_reason=args.blocked_reason,
            )
            payload = _assignment_payload(state, assignment)
        else:
            raise AssertionError(f"Unsupported command: {args.command}")
    except TaskCenterCommandError as error:
        print(json.dumps({"ok": False, "error": str(error)}, ensure_ascii=False), file=sys.stderr)
        return 2

    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0


class TaskCenterCommandError(Exception):
    """Expected task-center command failure."""


def _resolve_state_dir(args) -> Path:
    if args.state_dir:
        return Path(args.state_dir).expanduser().resolve()
    return Path(args.project_root).expanduser().resolve() / ".conductor" / "state"


def _resolve_state(store: FileStateStore, project_id: str | None) -> SharedProjectState:
    if project_id:
        try:
            return store.get_state(project_id)
        except KeyError as error:
            raise TaskCenterCommandError(f"Project not found: {project_id}") from error
    states = store.list_states()
    if not states:
        raise TaskCenterCommandError("No project state found.")
    if len(states) > 1:
        raise TaskCenterCommandError("Multiple project states found. Pass --project-id.")
    return states[0]


def _list_payload(state: SharedProjectState, status: str | None = None) -> dict[str, object]:
    assignments = [
        assignment
        for assignment in state.task_assignments
        if status is None or assignment.status.value == status
    ]
    return {
        "ok": True,
        "project_id": state.project.id,
        "status_filter": status or "",
        "total": len(assignments),
        "tasks": [_assignment_payload(state, assignment)["task"] for assignment in assignments],
    }


def _claim_assignment(
    store: FileStateStore,
    state: SharedProjectState,
    assignment_id: str,
    agent_id: str,
    claim_reason: str = "",
) -> tuple[SharedProjectState, TaskAssignment]:
    assignment = _require_assignment(state, assignment_id)
    if assignment.status != TaskAssignmentStatus.QUEUED:
        raise TaskCenterCommandError(f"Task assignment is not queued: {assignment.status.value}")
    updated = replace(
        assignment,
        status=TaskAssignmentStatus.CLAIMED,
        assigned_agent_id=agent_id,
        claim_reason=claim_reason or assignment.claim_reason,
        blocked_reason=None,
    )
    store.upsert_task_assignment(state.project.id, updated)
    store.add_event(state.project.id, f"TaskCenterCLI: {agent_id} claimed {assignment.workitem_id}")
    return store.get_state(state.project.id), updated


def _select_next_assignment(state: SharedProjectState, role: str | None = None) -> TaskAssignment:
    """Return the first queued assignment whose dependencies are satisfied."""
    for assignment in state.task_assignments:
        if assignment.status != TaskAssignmentStatus.QUEUED:
            continue
        if role and assignment.role != role:
            continue
        if not _dependencies_satisfied(state, assignment):
            continue
        return assignment
    suffix = f" for role {role}" if role else ""
    raise TaskCenterCommandError(f"No queued task assignment available{suffix}.")


def _dependencies_satisfied(state: SharedProjectState, assignment: TaskAssignment) -> bool:
    """Return whether all referenced WorkItem dependencies are done."""
    if not assignment.dependencies:
        return True
    status_by_workitem = {item.id: item.status for item in state.workitems}
    return all(status_by_workitem.get(dependency_id) == WorkItemStatus.DONE for dependency_id in assignment.dependencies)


def _return_assignment(
    store: FileStateStore,
    state: SharedProjectState,
    assignment_id: str,
    status: TaskAssignmentStatus,
    result_summary: str = "",
    output_artifact_ids: list[str] | None = None,
    blocked_reason: str = "",
) -> tuple[SharedProjectState, TaskAssignment]:
    assignment = _require_assignment(state, assignment_id)
    if assignment.status != TaskAssignmentStatus.CLAIMED:
        raise TaskCenterCommandError(f"Task assignment is not claimed: {assignment.status.value}")
    updated = replace(
        assignment,
        status=status,
        result_summary=result_summary,
        output_artifact_ids=list(output_artifact_ids or []),
        blocked_reason=blocked_reason or None,
    )
    store.upsert_task_assignment(state.project.id, updated)
    store.add_event(state.project.id, f"TaskCenterCLI: {assignment.workitem_id} returned {status.value}")
    return store.get_state(state.project.id), updated


def _require_assignment(state: SharedProjectState, assignment_id: str) -> TaskAssignment:
    for assignment in state.task_assignments:
        if assignment.id == assignment_id:
            return assignment
    raise TaskCenterCommandError(f"Task assignment not found: {assignment_id}")


def _assignment_payload(state: SharedProjectState, assignment: TaskAssignment) -> dict[str, object]:
    workitem = next((item for item in state.workitems if item.id == assignment.workitem_id), None)
    return {
        "ok": True,
        "project_id": state.project.id,
        "task": {
            **asdict(assignment),
            "status": assignment.status.value,
            "workitem": asdict(workitem) if workitem else {},
        },
    }


if __name__ == "__main__":
    raise SystemExit(main())
