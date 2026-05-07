"""Task Center CLI for persisted Conductor project states."""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict
from pathlib import Path

from conductor.artifacts.store import ArtifactStore
from conductor.domain.models import SharedProjectState, TaskAssignment, TaskAssignmentStatus
from conductor.io.encoding import configure_utf8_stdio
from conductor.task_center.artifacts import create_task_return_artifact
from conductor.task_center.context import TaskContextBuilder
from conductor.state.file_store import FileStateStore
from conductor.task_center.service import TaskCenterError, TaskCenterService

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

    subparsers.add_parser("summary", parents=[common], help="Print task-center summary counts.")

    context_parser = subparsers.add_parser("context", parents=[common], help="Print one assignment with input artifact content.")
    context_parser.add_argument("assignment_id")
    context_parser.add_argument("--no-content", action="store_true", help="Only print artifact metadata.")
    context_parser.add_argument("--max-content-chars", type=int, default=12000)
    context_parser.add_argument("--format", choices=["json", "markdown"], default="json")

    claim_parser = subparsers.add_parser("claim", parents=[common], help="Claim one queued assignment.")
    claim_parser.add_argument("assignment_id")
    claim_parser.add_argument("--agent-id", required=True)
    claim_parser.add_argument("--claim-reason", default="")
    claim_parser.add_argument("--with-context", action="store_true", help="Include input artifact context in the claim response.")
    claim_parser.add_argument("--no-content", action="store_true", help="Only print artifact metadata with --with-context.")
    claim_parser.add_argument("--max-content-chars", type=int, default=12000)

    claim_next_parser = subparsers.add_parser("claim-next", parents=[common], help="Claim the next queued assignment.")
    claim_next_parser.add_argument("--agent-id", required=True)
    claim_next_parser.add_argument("--role", help="Only claim assignments for this role.")
    claim_next_parser.add_argument("--claim-reason", default="")
    claim_next_parser.add_argument("--with-context", action="store_true", help="Include input artifact context in the claim response.")
    claim_next_parser.add_argument("--no-content", action="store_true", help="Only print artifact metadata with --with-context.")
    claim_next_parser.add_argument("--max-content-chars", type=int, default=12000)

    complete_parser = subparsers.add_parser("complete", parents=[common], help="Return one claimed assignment as completed.")
    complete_parser.add_argument("assignment_id")
    complete_parser.add_argument("--result-summary", default="")
    complete_parser.add_argument("--output-artifact-id", action="append", default=[])
    complete_parser.add_argument("--output-file", help="Create an output artifact from a UTF-8 file.")
    complete_parser.add_argument("--output-artifact-kind", default="external_result")
    complete_parser.add_argument("--output-artifact-title", default="")

    fail_parser = subparsers.add_parser("fail", parents=[common], help="Return one claimed assignment as failed.")
    fail_parser.add_argument("assignment_id")
    fail_parser.add_argument("--result-summary", default="")
    fail_parser.add_argument("--blocked-reason", default="")
    fail_parser.add_argument("--output-artifact-id", action="append", default=[])
    fail_parser.add_argument("--output-file", help="Create an output artifact from a UTF-8 file.")
    fail_parser.add_argument("--output-artifact-kind", default="external_result")
    fail_parser.add_argument("--output-artifact-title", default="")
    return parser


def main(argv: list[str] | None = None) -> int:
    """Run the task-center command and print a JSON payload."""
    args = build_parser().parse_args(argv)
    store = FileStateStore(_resolve_state_dir(args))
    state = _resolve_state(store, args.project_id)
    service = TaskCenterService(store, event_prefix="TaskCenterCLI")

    try:
        if args.command == "list":
            payload = _list_payload(
                state,
                service.list_assignments(state.project.id, status=args.status),
                service,
                status=args.status,
            )
        elif args.command == "summary":
            payload = _summary_payload(state, service)
        elif args.command == "context":
            context_builder = TaskContextBuilder()
            payload = context_builder.build(
                state,
                args.assignment_id,
                service=service,
                include_content=not args.no_content,
                max_content_chars=args.max_content_chars,
            )
            if args.format == "markdown":
                payload = context_builder.render_markdown(payload)
        elif args.command == "claim":
            result = service.claim(
                state.project.id,
                assignment_id=args.assignment_id,
                agent_id=args.agent_id,
                claim_reason=args.claim_reason,
            )
            payload = _assignment_payload(result.state, result.assignment, service)
            payload = _attach_context_if_requested(payload, args, result.state, result.assignment, service)
        elif args.command == "claim-next":
            result = service.claim_next(
                state.project.id,
                agent_id=args.agent_id,
                role=args.role,
                claim_reason=args.claim_reason,
            )
            payload = _assignment_payload(result.state, result.assignment, service)
            payload = _attach_context_if_requested(payload, args, result.state, result.assignment, service)
        elif args.command == "complete":
            output_artifact_ids = _return_output_artifact_ids(args, store, state, service)
            result = service.complete(
                state.project.id,
                assignment_id=args.assignment_id,
                result_summary=args.result_summary,
                output_artifact_ids=output_artifact_ids,
            )
            payload = _assignment_payload(result.state, result.assignment, service)
        elif args.command == "fail":
            output_artifact_ids = _return_output_artifact_ids(args, store, state, service)
            result = service.fail(
                state.project.id,
                assignment_id=args.assignment_id,
                result_summary=args.result_summary,
                output_artifact_ids=output_artifact_ids,
                blocked_reason=args.blocked_reason,
            )
            payload = _assignment_payload(result.state, result.assignment, service)
        else:
            raise AssertionError(f"Unsupported command: {args.command}")
    except TaskCenterError as error:
        print(json.dumps({"ok": False, "error": str(error)}, ensure_ascii=False), file=sys.stderr)
        return 2
    except ValueError as error:
        print(json.dumps({"ok": False, "error": str(error)}, ensure_ascii=False), file=sys.stderr)
        return 2

    if isinstance(payload, str):
        print(payload, end="")
    else:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0


def _resolve_state_dir(args) -> Path:
    if args.state_dir:
        return Path(args.state_dir).expanduser().resolve()
    return Path(args.project_root).expanduser().resolve() / ".conductor" / "state"


def _resolve_state(store: FileStateStore, project_id: str | None) -> SharedProjectState:
    if project_id:
        try:
            return store.get_state(project_id)
        except KeyError as error:
            raise TaskCenterError(f"Project not found: {project_id}", status_code=404) from error
    states = store.list_states()
    if not states:
        raise TaskCenterError("No project state found.", status_code=404)
    if len(states) > 1:
        raise TaskCenterError("Multiple project states found. Pass --project-id.")
    return states[0]


def _list_payload(
    state: SharedProjectState,
    assignments: list[TaskAssignment],
    service: TaskCenterService,
    status: str | None = None,
) -> dict[str, object]:
    return {
        "ok": True,
        "project_id": state.project.id,
        "status_filter": status or "",
        "total": len(assignments),
        "summary": service.summary(state),
        "tasks": [_assignment_payload(state, assignment, service)["task"] for assignment in assignments],
    }


def _summary_payload(state: SharedProjectState, service: TaskCenterService) -> dict[str, object]:
    return {
        "ok": True,
        "project_id": state.project.id,
        "summary": service.summary(state),
    }


def _return_output_artifact_ids(
    args,
    store: FileStateStore,
    state: SharedProjectState,
    service: TaskCenterService,
) -> list[str]:
    output_artifact_ids = list(args.output_artifact_id)
    if not args.output_file:
        return output_artifact_ids
    assignment = service.require_assignment(state, args.assignment_id)
    content = Path(args.output_file).expanduser().read_text(encoding="utf-8")
    artifact = create_task_return_artifact(
        state_store=store,
        artifact_store=ArtifactStore(),
        state=state,
        assignment=assignment,
        content=content,
        kind=args.output_artifact_kind,
        title=args.output_artifact_title,
    )
    output_artifact_ids.append(artifact.id)
    return output_artifact_ids


def _attach_context_if_requested(
    payload: dict[str, object],
    args,
    state: SharedProjectState,
    assignment: TaskAssignment,
    service: TaskCenterService,
) -> dict[str, object]:
    if not getattr(args, "with_context", False):
        return payload
    payload["context"] = TaskContextBuilder().build(
        state,
        assignment.id,
        service=service,
        include_content=not getattr(args, "no_content", False),
        max_content_chars=getattr(args, "max_content_chars", 12000),
    )
    return payload


def _assignment_payload(
    state: SharedProjectState,
    assignment: TaskAssignment,
    service: TaskCenterService,
) -> dict[str, object]:
    workitem = next((item for item in state.workitems if item.id == assignment.workitem_id), None)
    return {
        "ok": True,
        "project_id": state.project.id,
        "summary": service.summary(state),
        "task": {
            **asdict(assignment),
            "status": assignment.status.value,
            "claimable": service.claimable(state, assignment),
            "unmet_dependency_ids": service.unmet_dependency_ids(state, assignment),
            "workitem": asdict(workitem) if workitem else {},
        },
    }


if __name__ == "__main__":
    raise SystemExit(main())
