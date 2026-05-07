"""Task Center CLI for persisted Conductor project states."""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict, replace
from pathlib import Path

from conductor.artifacts.store import ArtifactStore
from conductor.domain.models import SharedProjectState, TaskAssignment, TaskAssignmentStatus
from conductor.io.encoding import configure_utf8_stdio
from conductor.task_center.artifacts import create_task_return_artifact
from conductor.task_center.context import TaskContextBuilder
from conductor.task_center.prompts import resolve_task_prompt_path, write_task_prompt_file
from conductor.state.file_store import FileStateStore
from conductor.state.store import InMemoryStateStore
from conductor.task_center.service import DEFAULT_STALE_CLAIMED_AFTER_SECONDS, TaskCenterError, TaskCenterService

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
    list_parser.add_argument("--stale-only", action="store_true", help="Only list stale claimed assignments.")
    list_parser.add_argument("--stale-after-seconds", type=int, default=DEFAULT_STALE_CLAIMED_AFTER_SECONDS)

    summary_parser = subparsers.add_parser("summary", parents=[common], help="Print task-center summary counts.")
    summary_parser.add_argument("--stale-after-seconds", type=int, default=DEFAULT_STALE_CLAIMED_AFTER_SECONDS)

    context_parser = subparsers.add_parser("context", parents=[common], help="Print one assignment with input artifact content.")
    context_parser.add_argument("assignment_id")
    context_parser.add_argument("--no-content", action="store_true", help="Only print artifact metadata.")
    context_parser.add_argument("--max-content-chars", type=int, default=12000)
    context_parser.add_argument("--format", choices=["json", "markdown"], default="json")
    context_parser.add_argument("--prompt-file", help="Write Markdown context to a file. Relative paths use project root.")

    claim_parser = subparsers.add_parser("claim", parents=[common], help="Claim one queued assignment.")
    claim_parser.add_argument("assignment_id")
    claim_parser.add_argument("--agent-id", required=True)
    claim_parser.add_argument("--claim-reason", default="")
    claim_parser.add_argument("--with-context", action="store_true", help="Include input artifact context in the claim response.")
    claim_parser.add_argument("--no-content", action="store_true", help="Only print artifact metadata with --with-context.")
    claim_parser.add_argument("--max-content-chars", type=int, default=12000)
    claim_parser.add_argument("--context-format", choices=["json", "markdown"], default="json")
    claim_parser.add_argument("--prompt-file", help="Write Markdown context to a file. Relative paths use project root.")

    claim_next_parser = subparsers.add_parser("claim-next", parents=[common], help="Claim the next queued assignment.")
    claim_next_parser.add_argument("--agent-id", required=True)
    claim_next_parser.add_argument("--role", help="Only claim assignments for this role.")
    claim_next_parser.add_argument("--claim-reason", default="")
    claim_next_parser.add_argument("--with-context", action="store_true", help="Include input artifact context in the claim response.")
    claim_next_parser.add_argument("--no-content", action="store_true", help="Only print artifact metadata with --with-context.")
    claim_next_parser.add_argument("--max-content-chars", type=int, default=12000)
    claim_next_parser.add_argument("--context-format", choices=["json", "markdown"], default="json")
    claim_next_parser.add_argument("--prompt-file", help="Write Markdown context to a file. Relative paths use project root.")

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

    heartbeat_parser = subparsers.add_parser("heartbeat", parents=[common], help="Refresh one claimed assignment heartbeat.")
    heartbeat_parser.add_argument("assignment_id")
    heartbeat_parser.add_argument("--agent-id", default="", help="Optional agent id guard for the current claimant.")

    release_parser = subparsers.add_parser("release", parents=[common], help="Release a claimed/failed assignment back to queued.")
    release_parser.add_argument("assignment_id")
    release_parser.add_argument("--release-reason", default="")

    release_stale_parser = subparsers.add_parser("release-stale", parents=[common], help="Release stale claimed assignments back to queued.")
    release_stale_parser.add_argument("--stale-after-seconds", type=int, default=DEFAULT_STALE_CLAIMED_AFTER_SECONDS)
    release_stale_parser.add_argument("--release-reason", default="stale claimed assignment")
    return parser


def main(argv: list[str] | None = None) -> int:
    """Run the task-center command and print a JSON payload."""
    args = build_parser().parse_args(argv)
    store = FileStateStore(_resolve_state_dir(args))
    state = _resolve_state(store, args.project_id)
    service = TaskCenterService(store, event_prefix="TaskCenterCLI")

    try:
        _validate_prompt_file_before_mutation(args, state)
        if args.command == "list":
            payload = _list_payload(
                state,
                service.list_assignments(state.project.id, status=args.status),
                service,
                status=args.status,
                stale_only=args.stale_only,
                stale_after_seconds=args.stale_after_seconds,
            )
        elif args.command == "summary":
            payload = _summary_payload(state, service, stale_after_seconds=args.stale_after_seconds)
        elif args.command == "context":
            context_builder = TaskContextBuilder()
            payload = context_builder.build(
                state,
                args.assignment_id,
                service=service,
                include_content=not args.no_content,
                max_content_chars=args.max_content_chars,
            )
            prompt_markdown = ""
            if args.prompt_file or args.format == "markdown":
                prompt_markdown = context_builder.render_markdown(payload)
            if args.prompt_file:
                prompt_file = write_task_prompt_file(args.prompt_file, state.project.project_root, prompt_markdown)
                _record_assignment_prompt_file(store, state, args.assignment_id, prompt_file)
                payload["prompt_file"] = str(prompt_file)
                payload["assignment"]["prompt_file"] = str(prompt_file)
            if args.format == "markdown":
                payload = prompt_markdown
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
        elif args.command == "heartbeat":
            result = service.heartbeat(
                state.project.id,
                assignment_id=args.assignment_id,
                agent_id=args.agent_id,
            )
            payload = _assignment_payload(result.state, result.assignment, service)
        elif args.command == "release":
            result = service.release(
                state.project.id,
                assignment_id=args.assignment_id,
                release_reason=args.release_reason,
            )
            payload = _assignment_payload(result.state, result.assignment, service)
        elif args.command == "release-stale":
            result = service.release_stale(
                state.project.id,
                stale_after_seconds=args.stale_after_seconds,
                release_reason=args.release_reason,
            )
            payload = _bulk_release_payload(result.state, result.assignments, service, args.stale_after_seconds)
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


def _validate_prompt_file_before_mutation(args, state: SharedProjectState) -> None:
    prompt_file = getattr(args, "prompt_file", "")
    if not prompt_file or args.command not in {"claim", "claim-next"}:
        return
    resolve_task_prompt_path(prompt_file, state.project.project_root)


def _list_payload(
    state: SharedProjectState,
    assignments: list[TaskAssignment],
    service: TaskCenterService,
    status: str | None = None,
    stale_only: bool = False,
    stale_after_seconds: int = DEFAULT_STALE_CLAIMED_AFTER_SECONDS,
) -> dict[str, object]:
    if stale_only:
        assignments = [
            assignment
            for assignment in assignments
            if service.stale_claimed(assignment, stale_after_seconds=stale_after_seconds)
        ]
    return {
        "ok": True,
        "project_id": state.project.id,
        "status_filter": status or "",
        "stale_only": stale_only,
        "stale_after_seconds": stale_after_seconds,
        "total": len(assignments),
        "summary": service.summary(state, stale_after_seconds=stale_after_seconds),
        "tasks": [
            _assignment_payload(state, assignment, service, stale_after_seconds=stale_after_seconds)["task"]
            for assignment in assignments
        ],
    }


def _summary_payload(
    state: SharedProjectState,
    service: TaskCenterService,
    stale_after_seconds: int = DEFAULT_STALE_CLAIMED_AFTER_SECONDS,
) -> dict[str, object]:
    return {
        "ok": True,
        "project_id": state.project.id,
        "stale_after_seconds": stale_after_seconds,
        "summary": service.summary(state, stale_after_seconds=stale_after_seconds),
    }


def _bulk_release_payload(
    state: SharedProjectState,
    assignments: list[TaskAssignment],
    service: TaskCenterService,
    stale_after_seconds: int,
) -> dict[str, object]:
    return {
        "ok": True,
        "project_id": state.project.id,
        "released_count": len(assignments),
        "stale_after_seconds": stale_after_seconds,
        "summary": service.summary(state, stale_after_seconds=stale_after_seconds),
        "tasks": [
            _assignment_payload(state, assignment, service, stale_after_seconds=stale_after_seconds)["task"]
            for assignment in assignments
        ],
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
) -> dict[str, object] | str:
    prompt_file_arg = getattr(args, "prompt_file", None)
    if not getattr(args, "with_context", False) and not prompt_file_arg:
        return payload
    context_builder = TaskContextBuilder()
    context = context_builder.build(
        state,
        assignment.id,
        service=service,
        include_content=not getattr(args, "no_content", False),
        max_content_chars=getattr(args, "max_content_chars", 12000),
    )
    prompt_markdown = ""
    if prompt_file_arg or getattr(args, "context_format", "json") == "markdown":
        prompt_markdown = context_builder.render_markdown(context)
    if prompt_file_arg:
        prompt_file = write_task_prompt_file(prompt_file_arg, state.project.project_root, prompt_markdown)
        _record_assignment_prompt_file(service.state_store, state, assignment.id, prompt_file)
        payload["prompt_file"] = str(prompt_file)
        if isinstance(payload.get("task"), dict):
            payload["task"]["prompt_file"] = str(prompt_file)
    if getattr(args, "context_format", "json") == "markdown":
        return prompt_markdown
    if getattr(args, "with_context", False):
        payload["context"] = context
    return payload


def _record_assignment_prompt_file(
    store: InMemoryStateStore,
    state: SharedProjectState,
    assignment_id: str,
    prompt_file: Path,
) -> None:
    assignment = next((item for item in state.task_assignments if item.id == assignment_id), None)
    if assignment is None:
        raise TaskCenterError(f"Task assignment not found: {assignment_id}", status_code=404)
    store.upsert_task_assignment(state.project.id, replace(assignment, prompt_file=str(prompt_file)))


def _assignment_payload(
    state: SharedProjectState,
    assignment: TaskAssignment,
    service: TaskCenterService,
    stale_after_seconds: int = DEFAULT_STALE_CLAIMED_AFTER_SECONDS,
) -> dict[str, object]:
    workitem = next((item for item in state.workitems if item.id == assignment.workitem_id), None)
    claimed_age_seconds = service.claimed_age_seconds(assignment)
    heartbeat_age_seconds = service.heartbeat_age_seconds(assignment)
    return {
        "ok": True,
        "project_id": state.project.id,
        "summary": service.summary(state, stale_after_seconds=stale_after_seconds),
        "task": {
            **asdict(assignment),
            "status": assignment.status.value,
            "assigned_agent_id": assignment.assigned_agent_id or "",
            "blocked_reason": assignment.blocked_reason or "",
            "claimable": service.claimable(state, assignment),
            "unmet_dependency_ids": service.unmet_dependency_ids(state, assignment),
            "claimed_age_seconds": claimed_age_seconds,
            "heartbeat_age_seconds": heartbeat_age_seconds,
            "stale_claimed": service.stale_claimed(assignment, stale_after_seconds=stale_after_seconds),
            "workitem": asdict(workitem) if workitem else {},
        },
    }


if __name__ == "__main__":
    raise SystemExit(main())
