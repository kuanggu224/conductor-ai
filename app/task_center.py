"""Task Center CLI for persisted Conductor project states."""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict, replace
from datetime import datetime, timezone
from pathlib import Path

from conductor.artifacts.store import ArtifactStore
from conductor.control.human import HumanControlService
from conductor.domain.models import SharedProjectState, TaskAssignment, TaskAssignmentStatus
from conductor.io.encoding import configure_utf8_stdio
from conductor.task_center.artifacts import create_task_return_artifact
from conductor.task_center.context import TaskContextBuilder
from conductor.task_center.prompts import resolve_task_prompt_path, write_task_prompt_file
from conductor.state.file_store import FileStateStore
from conductor.state.store import InMemoryStateStore
from conductor.task_center.service import (
    DEFAULT_MAX_BULK_CLAIM_LIMIT,
    DEFAULT_STALE_CLAIMED_AFTER_SECONDS,
    TaskCenterError,
    TaskCenterService,
)

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

    audit_parser = subparsers.add_parser("audit", parents=[common], help="Audit Task Center lifecycle integrity.")
    audit_parser.add_argument("--stale-after-seconds", type=int, default=DEFAULT_STALE_CLAIMED_AFTER_SECONDS)
    audit_parser.add_argument("--fail-on-findings", action="store_true", help="Exit with code 3 when findings exist.")

    audit_all_parser = subparsers.add_parser(
        "audit-all",
        parents=[common],
        help="Audit Task Center lifecycle integrity across every project in the state directory.",
    )
    audit_all_parser.add_argument("--stale-after-seconds", type=int, default=DEFAULT_STALE_CLAIMED_AFTER_SECONDS)
    audit_all_parser.add_argument("--fail-on-findings", action="store_true", help="Exit with code 3 when findings exist.")
    audit_all_parser.add_argument(
        "--output",
        help="Write the audit-all JSON maintenance report to a file. Relative paths use --project-root.",
    )

    context_parser = subparsers.add_parser("context", parents=[common], help="Print one assignment with input artifact content.")
    context_parser.add_argument("assignment_id")
    context_parser.add_argument("--no-content", action="store_true", help="Only print artifact metadata.")
    context_parser.add_argument("--max-content-chars", type=int, default=12000)
    context_parser.add_argument("--format", choices=["json", "markdown"], default="json")
    context_parser.add_argument("--prompt-file", help="Write Markdown context to a file. Relative paths use project root.")

    agents_for_parser = subparsers.add_parser(
        "agents-for",
        parents=[common],
        help="List dynamic Agent activations eligible for one assignment.",
    )
    agents_for_parser.add_argument("assignment_id")

    tasks_for_agent_parser = subparsers.add_parser(
        "tasks-for-agent",
        parents=[common],
        help="List assignments that match one dynamic Agent activation.",
    )
    tasks_for_agent_parser.add_argument("agent_id")
    tasks_for_agent_parser.add_argument("--claimable-only", action="store_true", help="Only return currently claimable tasks.")

    claim_for_agent_parser = subparsers.add_parser(
        "claim-for-agent",
        parents=[common],
        help="Claim the next assignment that matches one dynamic Agent activation.",
    )
    claim_for_agent_parser.add_argument("agent_id")
    claim_for_agent_parser.add_argument("--claim-reason", default="")
    claim_for_agent_parser.add_argument("--lease-seconds", type=int, default=0, help="Optional explicit claim lease duration.")
    claim_for_agent_parser.add_argument("--with-context", action="store_true", help="Include input artifact context in the claim response.")
    claim_for_agent_parser.add_argument("--no-content", action="store_true", help="Only print artifact metadata with --with-context.")
    claim_for_agent_parser.add_argument("--max-content-chars", type=int, default=12000)
    claim_for_agent_parser.add_argument("--context-format", choices=["json", "markdown"], default="json")
    claim_for_agent_parser.add_argument("--prompt-file", help="Write Markdown context to a file. Relative paths use project root.")

    claim_parser = subparsers.add_parser("claim", parents=[common], help="Claim one queued assignment.")
    claim_parser.add_argument("assignment_id")
    claim_parser.add_argument("--agent-id", required=True)
    claim_parser.add_argument("--claim-reason", default="")
    claim_parser.add_argument("--lease-seconds", type=int, default=0, help="Optional explicit claim lease duration.")
    claim_parser.add_argument("--with-context", action="store_true", help="Include input artifact context in the claim response.")
    claim_parser.add_argument("--no-content", action="store_true", help="Only print artifact metadata with --with-context.")
    claim_parser.add_argument("--max-content-chars", type=int, default=12000)
    claim_parser.add_argument("--context-format", choices=["json", "markdown"], default="json")
    claim_parser.add_argument("--prompt-file", help="Write Markdown context to a file. Relative paths use project root.")

    claim_next_parser = subparsers.add_parser("claim-next", parents=[common], help="Claim the next queued assignment.")
    claim_next_parser.add_argument("--agent-id", required=True)
    claim_next_parser.add_argument("--role", help="Only claim assignments for this role.")
    claim_next_parser.add_argument("--claim-reason", default="")
    claim_next_parser.add_argument("--lease-seconds", type=int, default=0, help="Optional explicit claim lease duration.")
    claim_next_parser.add_argument("--with-context", action="store_true", help="Include input artifact context in the claim response.")
    claim_next_parser.add_argument("--no-content", action="store_true", help="Only print artifact metadata with --with-context.")
    claim_next_parser.add_argument("--max-content-chars", type=int, default=12000)
    claim_next_parser.add_argument("--context-format", choices=["json", "markdown"], default="json")
    claim_next_parser.add_argument("--prompt-file", help="Write Markdown context to a file. Relative paths use project root.")

    claim_batch_parser = subparsers.add_parser("claim-batch", parents=[common], help="Claim a limited batch of queued assignments.")
    claim_batch_parser.add_argument("--agent-id", required=True)
    claim_batch_parser.add_argument("--role", help="Only claim assignments for this role.")
    claim_batch_parser.add_argument("--claim-reason", default="")
    claim_batch_parser.add_argument("--limit", type=int, default=1)
    claim_batch_parser.add_argument("--max-limit", type=int, default=DEFAULT_MAX_BULK_CLAIM_LIMIT)
    claim_batch_parser.add_argument("--lease-seconds", type=int, default=0, help="Optional explicit claim lease duration.")

    complete_parser = subparsers.add_parser("complete", parents=[common], help="Return one claimed assignment as completed.")
    complete_parser.add_argument("assignment_id")
    complete_parser.add_argument("--agent-id", default="", help="Optional agent id guard for the current claimant.")
    complete_parser.add_argument("--claim-token", default="", help="Optional claim token guard from the claim response.")
    complete_parser.add_argument("--result-summary", default="")
    complete_parser.add_argument("--output-artifact-id", action="append", default=[])
    complete_parser.add_argument("--output-file", help="Create an output artifact from a UTF-8 file.")
    complete_parser.add_argument("--output-artifact-kind", default="external_result")
    complete_parser.add_argument("--output-artifact-title", default="")

    fail_parser = subparsers.add_parser("fail", parents=[common], help="Return one claimed assignment as failed.")
    fail_parser.add_argument("assignment_id")
    fail_parser.add_argument("--agent-id", default="", help="Optional agent id guard for the current claimant.")
    fail_parser.add_argument("--claim-token", default="", help="Optional claim token guard from the claim response.")
    fail_parser.add_argument("--result-summary", default="")
    fail_parser.add_argument("--blocked-reason", default="")
    fail_parser.add_argument("--output-artifact-id", action="append", default=[])
    fail_parser.add_argument("--output-file", help="Create an output artifact from a UTF-8 file.")
    fail_parser.add_argument("--output-artifact-kind", default="external_result")
    fail_parser.add_argument("--output-artifact-title", default="")

    heartbeat_parser = subparsers.add_parser("heartbeat", parents=[common], help="Refresh one claimed assignment heartbeat.")
    heartbeat_parser.add_argument("assignment_id")
    heartbeat_parser.add_argument("--agent-id", default="", help="Optional agent id guard for the current claimant.")
    heartbeat_parser.add_argument("--claim-token", default="", help="Optional claim token guard from the claim response.")
    heartbeat_parser.add_argument("--lease-seconds", type=int, help="Override the existing lease duration while heartbeating.")

    release_parser = subparsers.add_parser("release", parents=[common], help="Release a claimed/failed assignment back to queued.")
    release_parser.add_argument("assignment_id")
    release_parser.add_argument("--agent-id", default="", help="Optional agent id guard for the current claimant.")
    release_parser.add_argument("--claim-token", default="", help="Optional claim token guard from the claim response.")
    release_parser.add_argument("--release-reason", default="")

    release_stale_parser = subparsers.add_parser("release-stale", parents=[common], help="Release stale claimed assignments back to queued.")
    release_stale_parser.add_argument("--stale-after-seconds", type=int, default=DEFAULT_STALE_CLAIMED_AFTER_SECONDS)
    release_stale_parser.add_argument("--release-reason", default="stale claimed assignment")

    release_expired_parser = subparsers.add_parser(
        "release-expired-leases",
        parents=[common],
        help="Release assignments whose explicit claim lease expired.",
    )
    release_expired_parser.add_argument("--release-reason", default="expired task lease")

    sweep_parser = subparsers.add_parser(
        "sweep",
        parents=[common],
        help="Run Task Center maintenance: release expired leases and stale claims.",
    )
    sweep_parser.add_argument("--stale-after-seconds", type=int, default=DEFAULT_STALE_CLAIMED_AFTER_SECONDS)
    sweep_parser.add_argument("--expired-lease-release-reason", default="expired task lease")
    sweep_parser.add_argument("--stale-release-reason", default="stale claimed assignment")

    sweep_all_parser = subparsers.add_parser(
        "sweep-all",
        parents=[common],
        help="Run Task Center maintenance across every project in the state directory.",
    )
    sweep_all_parser.add_argument("--stale-after-seconds", type=int, default=DEFAULT_STALE_CLAIMED_AFTER_SECONDS)
    sweep_all_parser.add_argument("--expired-lease-release-reason", default="expired task lease")
    sweep_all_parser.add_argument("--stale-release-reason", default="stale claimed assignment")
    sweep_all_parser.add_argument(
        "--output",
        help="Write the sweep-all JSON maintenance report to a file. Relative paths use --project-root.",
    )

    maintenance_parser = subparsers.add_parser(
        "maintenance",
        parents=[common],
        help="Run sweep-all followed by audit-all and emit one maintenance report.",
    )
    maintenance_parser.add_argument("--stale-after-seconds", type=int, default=DEFAULT_STALE_CLAIMED_AFTER_SECONDS)
    maintenance_parser.add_argument("--expired-lease-release-reason", default="expired task lease")
    maintenance_parser.add_argument("--stale-release-reason", default="stale claimed assignment")
    maintenance_parser.add_argument("--fail-on-findings", action="store_true", help="Exit with code 3 when audit findings exist.")
    maintenance_parser.add_argument(
        "--output",
        help="Write the combined JSON maintenance report to a file. Relative paths use --project-root.",
    )
    maintenance_parser.add_argument(
        "--latest-output",
        help="Write a compact latest-maintenance pointer JSON. Relative paths use --project-root.",
    )

    maintenance_status_parser = subparsers.add_parser(
        "maintenance-status",
        parents=[common],
        help="Read the latest maintenance pointer and return a health summary.",
    )
    maintenance_status_parser.add_argument(
        "--latest",
        default=".conductor/maintenance/latest.json",
        help="Latest-maintenance pointer JSON path. Relative paths use --project-root.",
    )
    maintenance_status_parser.add_argument(
        "--max-age-seconds",
        type=int,
        default=0,
        help="Optional maximum acceptable age for the latest pointer. Zero disables age checks.",
    )
    maintenance_status_parser.add_argument(
        "--fail-on-findings",
        action="store_true",
        help="Exit with code 3 when latest maintenance status is unhealthy or stale.",
    )

    watchdog_parser = subparsers.add_parser(
        "watchdog",
        parents=[common],
        help="Check latest maintenance status and refresh maintenance when it is stale or unhealthy.",
    )
    watchdog_parser.add_argument(
        "--latest",
        default=".conductor/maintenance/latest.json",
        help="Latest-maintenance pointer JSON path to check. Relative paths use --project-root.",
    )
    watchdog_parser.add_argument(
        "--max-age-seconds",
        type=int,
        default=0,
        help="Optional maximum acceptable age for the latest pointer. Zero disables age checks.",
    )
    watchdog_parser.add_argument("--stale-after-seconds", type=int, default=DEFAULT_STALE_CLAIMED_AFTER_SECONDS)
    watchdog_parser.add_argument("--expired-lease-release-reason", default="expired task lease")
    watchdog_parser.add_argument("--stale-release-reason", default="stale claimed assignment")
    watchdog_parser.add_argument(
        "--output",
        default=".conductor/maintenance/report.json",
        help="Maintenance report path used when watchdog refreshes. Relative paths use --project-root.",
    )
    watchdog_parser.add_argument(
        "--latest-output",
        help="Latest pointer path used when watchdog refreshes. Defaults to --latest.",
    )
    watchdog_parser.add_argument(
        "--check-only",
        action="store_true",
        help="Only inspect latest maintenance status; do not run maintenance.",
    )
    watchdog_parser.add_argument(
        "--fail-on-unhealthy",
        action="store_true",
        help="Exit with code 3 when the final watchdog status is unhealthy.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    """Run the task-center command and print a JSON payload."""
    args = build_parser().parse_args(argv)
    store = FileStateStore(_resolve_state_dir(args))
    multi_project_commands = {"audit-all", "maintenance", "maintenance-status", "sweep-all", "watchdog"}
    state = None if args.command in multi_project_commands else _resolve_state(store, args.project_id)
    max_bulk_claim_limit = getattr(args, "max_limit", DEFAULT_MAX_BULK_CLAIM_LIMIT)
    service = TaskCenterService(
        store,
        event_prefix="TaskCenterCLI",
        require_claim_guard=True,
        max_bulk_claim_limit=max_bulk_claim_limit,
    )

    exit_code = 0
    try:
        if args.command == "sweep-all":
            payload = _sweep_all_payload(
                store,
                service,
                stale_after_seconds=args.stale_after_seconds,
                expired_lease_release_reason=args.expired_lease_release_reason,
                stale_release_reason=args.stale_release_reason,
            )
            if args.output:
                output_path = _write_json_output(args.output, args.project_root, payload)
                payload["output_path"] = str(output_path)
        elif args.command == "audit-all":
            payload = _audit_all_payload(store, service, stale_after_seconds=args.stale_after_seconds)
            if args.fail_on_findings and payload["finding_count"]:
                exit_code = 3
            if args.output:
                output_path = _write_json_output(args.output, args.project_root, payload)
                payload["output_path"] = str(output_path)
        elif args.command == "maintenance":
            payload = _maintenance_payload(
                store,
                service,
                stale_after_seconds=args.stale_after_seconds,
                expired_lease_release_reason=args.expired_lease_release_reason,
                stale_release_reason=args.stale_release_reason,
            )
            _attach_maintenance_operator_hints(
                payload,
                project_root=args.project_root,
                stale_after_seconds=args.stale_after_seconds,
                report_path=args.output or ".conductor/maintenance/report.json",
                latest_path=args.latest_output or ".conductor/maintenance/latest.json",
            )
            if args.fail_on_findings and payload["audit"]["finding_count"]:
                exit_code = 3
            if args.output:
                output_path = _write_json_output(args.output, args.project_root, payload)
                payload["output_path"] = str(output_path)
            if args.latest_output:
                latest_path = _write_latest_maintenance_output(args.latest_output, args.project_root, payload)
                payload["latest_output_path"] = str(latest_path)
        elif args.command == "maintenance-status":
            payload = _maintenance_status_payload(
                args.latest,
                args.project_root,
                max_age_seconds=args.max_age_seconds,
            )
            if not payload["exists"]:
                exit_code = 2
            elif args.fail_on_findings and not payload["healthy"]:
                exit_code = 3
        elif args.command == "watchdog":
            payload = _watchdog_payload(
                store,
                service,
                project_root=args.project_root,
                latest_path=args.latest,
                max_age_seconds=args.max_age_seconds,
                stale_after_seconds=args.stale_after_seconds,
                expired_lease_release_reason=args.expired_lease_release_reason,
                stale_release_reason=args.stale_release_reason,
                report_path=args.output,
                latest_output_path=args.latest_output or args.latest,
                check_only=args.check_only,
            )
            if args.fail_on_unhealthy and not payload["healthy"]:
                exit_code = 3
        else:
            assert state is not None
            _validate_prompt_file_before_mutation(args, state)
        if args.command in multi_project_commands:
            pass
        elif args.command == "list":
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
        elif args.command == "audit":
            findings = service.audit(state, stale_after_seconds=args.stale_after_seconds)
            payload = _audit_payload(state, findings, service, stale_after_seconds=args.stale_after_seconds)
            if args.fail_on_findings and findings:
                exit_code = 3
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
        elif args.command == "agents-for":
            context_builder = TaskContextBuilder()
            context_payload = context_builder.build(
                state,
                args.assignment_id,
                service=service,
                include_content=False,
                max_content_chars=0,
            )
            assignment = context_payload["assignment"]
            workitem = context_payload["workitem"]
            agents = context_payload["eligible_agent_activations"]
            payload = {
                "ok": True,
                "project_id": context_payload["project_id"],
                "assignment_id": args.assignment_id,
                "workitem_id": workitem["id"],
                "stage": workitem["stage"],
                "kind": workitem["kind"],
                "role": assignment["role"],
                "eligible_count": len(agents),
                "agents": agents,
            }
        elif args.command == "tasks-for-agent":
            payload = _tasks_for_agent_payload(
                state,
                service,
                agent_id=args.agent_id,
                claimable_only=args.claimable_only,
                project_root=args.project_root,
            )
        elif args.command == "claim-for-agent":
            tasks_payload = _tasks_for_agent_payload(
                state,
                service,
                agent_id=args.agent_id,
                claimable_only=True,
                project_root=args.project_root,
            )
            tasks = tasks_payload["tasks"]
            if not tasks:
                raise TaskCenterError(f"No claimable task assignment available for agent {args.agent_id}", status_code=404)
            assignment_id = tasks[0]["assignment_id"]
            result = service.claim(
                state.project.id,
                assignment_id=assignment_id,
                agent_id=args.agent_id,
                claim_reason=args.claim_reason,
                lease_seconds=args.lease_seconds,
            )
            payload = _assignment_payload(result.state, result.assignment, service)
            payload["matched_agent"] = {
                "agent_id": args.agent_id,
                "instance_id": tasks[0].get("instance_id", ""),
                "scope": tasks[0].get("scope", ""),
                "parallel_safe": tasks[0].get("parallel_safe", False),
                "write_scope": tasks[0].get("write_scope", []),
            }
            payload = _attach_context_if_requested(payload, args, result.state, result.assignment, service)
        elif args.command == "claim":
            result = service.claim(
                state.project.id,
                assignment_id=args.assignment_id,
                agent_id=args.agent_id,
                claim_reason=args.claim_reason,
                lease_seconds=args.lease_seconds,
            )
            payload = _assignment_payload(result.state, result.assignment, service)
            payload = _attach_context_if_requested(payload, args, result.state, result.assignment, service)
        elif args.command == "claim-next":
            result = service.claim_next(
                state.project.id,
                agent_id=args.agent_id,
                role=args.role,
                claim_reason=args.claim_reason,
                lease_seconds=args.lease_seconds,
            )
            payload = _assignment_payload(result.state, result.assignment, service)
            payload = _attach_context_if_requested(payload, args, result.state, result.assignment, service)
        elif args.command == "claim-batch":
            result = service.claim_batch(
                state.project.id,
                agent_id=args.agent_id,
                role=args.role,
                claim_reason=args.claim_reason,
                limit=args.limit,
                lease_seconds=args.lease_seconds,
            )
            payload = _bulk_claim_payload(result.state, result.assignments, service, args.limit)
        elif args.command == "complete":
            output_artifact_ids = _return_output_artifact_ids(args, store, state, service)
            result = service.complete(
                state.project.id,
                assignment_id=args.assignment_id,
                result_summary=args.result_summary,
                output_artifact_ids=output_artifact_ids,
                agent_id=args.agent_id,
                claim_token=args.claim_token,
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
                agent_id=args.agent_id,
                claim_token=args.claim_token,
            )
            payload = _assignment_payload(result.state, result.assignment, service)
        elif args.command == "heartbeat":
            result = service.heartbeat(
                state.project.id,
                assignment_id=args.assignment_id,
                agent_id=args.agent_id,
                claim_token=args.claim_token,
                lease_seconds=args.lease_seconds,
            )
            payload = _assignment_payload(result.state, result.assignment, service)
        elif args.command == "release":
            result = service.release(
                state.project.id,
                assignment_id=args.assignment_id,
                agent_id=args.agent_id,
                claim_token=args.claim_token,
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
        elif args.command == "release-expired-leases":
            result = service.release_expired_leases(
                state.project.id,
                release_reason=args.release_reason,
            )
            payload = _bulk_expired_lease_release_payload(result.state, result.assignments, service)
        elif args.command == "sweep":
            result = service.sweep(
                state.project.id,
                stale_after_seconds=args.stale_after_seconds,
                expired_lease_release_reason=args.expired_lease_release_reason,
                stale_release_reason=args.stale_release_reason,
            )
            payload = _sweep_payload(result.state, result, service, args.stale_after_seconds)
        else:
            raise AssertionError(f"Unsupported command: {args.command}")
    except TaskCenterError as error:
        print(json.dumps(_task_center_error_payload(error), ensure_ascii=False), file=sys.stderr)
        return 2
    except ValueError as error:
        print(json.dumps({"ok": False, "error": str(error), "status_code": 400}, ensure_ascii=False), file=sys.stderr)
        return 2

    if isinstance(payload, str):
        print(payload, end="")
    else:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    return exit_code


def _task_center_error_payload(error: TaskCenterError) -> dict[str, object]:
    """Return a machine-readable CLI error payload for external workers."""
    payload: dict[str, object] = {
        "ok": False,
        "error": str(error),
        "error_code": error.code,
        "status_code": error.status_code,
    }
    if error.details:
        payload["details"] = dict(error.details)
    return payload


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


def _audit_payload(
    state: SharedProjectState,
    findings,
    service: TaskCenterService,
    stale_after_seconds: int,
) -> dict[str, object]:
    error_count = sum(1 for finding in findings if finding.severity == "error")
    warning_count = sum(1 for finding in findings if finding.severity == "warning")
    return {
        "ok": True,
        "project_id": state.project.id,
        "passed": not findings,
        "finding_count": len(findings),
        "error_count": error_count,
        "warning_count": warning_count,
        "stale_after_seconds": stale_after_seconds,
        "summary": service.summary(state, stale_after_seconds=stale_after_seconds),
        "findings": [asdict(finding) for finding in findings],
    }


def _tasks_for_agent_payload(
    state: SharedProjectState,
    service: TaskCenterService,
    *,
    agent_id: str,
    claimable_only: bool,
    project_root: str,
) -> dict[str, object]:
    activations = [activation for activation in state.agent_activations if activation.agent_id == agent_id]
    if not activations:
        raise TaskCenterError(f"Agent activation not found: {agent_id}", status_code=404)
    workitems_by_id = {workitem.id: workitem for workitem in state.workitems}
    tasks: list[dict[str, object]] = []
    seen_assignment_ids: set[str] = set()
    for activation in activations:
        for assignment in state.task_assignments:
            if assignment.id in seen_assignment_ids or assignment.role != activation.role:
                continue
            workitem = workitems_by_id.get(assignment.workitem_id)
            if workitem is None:
                continue
            if activation.stage and activation.stage != workitem.stage:
                continue
            if activation.related_workitem_kinds and workitem.kind not in activation.related_workitem_kinds:
                continue
            claimable = service.claimable(state, assignment, agent_id=agent_id)
            if claimable_only and not claimable:
                continue
            write_scope_conflicts = service.write_scope_conflicts(state, assignment, agent_id=agent_id)
            seen_assignment_ids.add(assignment.id)
            tasks.append(
                {
                    "assignment_id": assignment.id,
                    "workitem_id": workitem.id,
                    "stage": workitem.stage,
                    "kind": workitem.kind,
                    "role": assignment.role,
                    "assignment_status": assignment.status.value,
                    "workitem_status": workitem.status.value,
                    "claimable": claimable,
                    "unmet_dependency_ids": service.unmet_dependency_ids(state, assignment),
                    "write_scope_conflict_assignment_ids": write_scope_conflicts,
                    "instance_id": activation.instance_id,
                    "scope": activation.scope,
                    "parallel_safe": activation.parallel_safe,
                    "write_scope": list(activation.write_scope),
                    "claim_command": _dynamic_agent_claim_command(
                        project_root=project_root,
                        agent_id=agent_id,
                        assignment_id=assignment.id,
                        with_context=False,
                    )
                    if claimable
                    else "",
                    "claim_with_context_command": _dynamic_agent_claim_command(
                        project_root=project_root,
                        agent_id=agent_id,
                        assignment_id=assignment.id,
                        with_context=True,
                    )
                    if claimable
                    else "",
                }
            )
    return {
        "ok": True,
        "project_id": state.project.id,
        "agent_id": agent_id,
        "activation_count": len(activations),
        "claimable_only": claimable_only,
        "task_count": len(tasks),
        "activations": [
            {
                "agent_id": activation.agent_id,
                "role": activation.role,
                "instance_id": activation.instance_id,
                "stage": activation.stage,
                "related_workitem_kinds": list(activation.related_workitem_kinds),
                "scope": activation.scope,
                "parallel_safe": activation.parallel_safe,
                "write_scope": list(activation.write_scope),
            }
            for activation in activations
        ],
        "tasks": tasks,
    }


def _dynamic_agent_claim_command(
    *,
    project_root: str,
    agent_id: str,
    assignment_id: str,
    with_context: bool,
) -> str:
    parts = [
        "python",
        "-m",
        "app.task_center",
        "claim",
        _quote_cli_arg(assignment_id),
        "--project-root",
        _quote_cli_arg(project_root),
        "--agent-id",
        _quote_cli_arg(agent_id),
    ]
    if with_context:
        parts.append("--with-context")
    return " ".join(parts)


def _audit_all_payload(
    store: FileStateStore,
    service: TaskCenterService,
    stale_after_seconds: int,
) -> dict[str, object]:
    project_results: list[dict[str, object]] = []
    total_errors = 0
    total_warnings = 0
    human_control = HumanControlService(service.state_store)
    for state in store.list_states():
        findings = service.audit(state, stale_after_seconds=stale_after_seconds)
        error_count = sum(1 for finding in findings if finding.severity == "error")
        warning_count = sum(1 for finding in findings if finding.severity == "warning")
        total_errors += error_count
        total_warnings += warning_count
        active_human_control = human_control.active_action(state)
        project_result: dict[str, object] = {
            "project_id": state.project.id,
            "passed": not findings,
            "finding_count": len(findings),
            "error_count": error_count,
            "warning_count": warning_count,
            "summary": service.summary(state, stale_after_seconds=stale_after_seconds),
            "pending_test_scope": list(state.pending_test_scope),
            "active_human_control_action": _human_control_action_payload(active_human_control),
            "findings": [asdict(finding) for finding in findings],
        }
        project_results.append(project_result)
    rollup = _audit_project_rollup(project_results)
    return {
        "ok": True,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "passed": total_errors + total_warnings == 0,
        "project_count": len(project_results),
        "finding_count": total_errors + total_warnings,
        "error_count": total_errors,
        "warning_count": total_warnings,
        "stale_after_seconds": stale_after_seconds,
        **rollup,
        "projects": project_results,
    }


def _audit_project_rollup(project_results: list[dict[str, object]]) -> dict[str, object]:
    """Return operator-focused rollups for multi-project audit reports."""
    attention_project_ids: list[str] = []
    finding_code_counts: dict[str, int] = {}
    recommendations: list[str] = []
    pending_retest_project_ids: list[str] = []
    pending_retest_scopes: dict[str, list[object]] = {}
    human_control_project_ids: list[str] = []
    active_human_control_actions: list[dict[str, object]] = []
    for project in project_results:
        project_id = str(project.get("project_id", ""))
        pending_scope = _json_list_payload(project.get("pending_test_scope"))
        if project_id and pending_scope:
            pending_retest_project_ids.append(project_id)
            pending_retest_scopes[project_id] = pending_scope
        active_human_control = project.get("active_human_control_action")
        if project_id and isinstance(active_human_control, dict) and active_human_control:
            human_control_project_ids.append(project_id)
            active_human_control_actions.append(active_human_control)
        findings = project.get("findings", [])
        if not isinstance(findings, list) or not findings:
            continue
        if project_id:
            attention_project_ids.append(project_id)
        for finding in findings:
            if not isinstance(finding, dict):
                continue
            code = str(finding.get("code", ""))
            if code:
                finding_code_counts[code] = finding_code_counts.get(code, 0) + 1
            recommendation = str(finding.get("recommendation", ""))
            if recommendation and recommendation not in recommendations:
                recommendations.append(recommendation)
    return {
        "attention_project_ids": attention_project_ids,
        "finding_code_counts": finding_code_counts,
        "recommendations": recommendations,
        "pending_retest_project_ids": pending_retest_project_ids,
        "pending_retest_scopes": pending_retest_scopes,
        "human_control_project_ids": human_control_project_ids,
        "active_human_control_actions": active_human_control_actions,
    }


def _human_control_action_payload(action) -> dict[str, object]:
    if action is None:
        return {}
    return {
        "id": action.id,
        "project_id": action.project_id,
        "action": action.action.value,
        "actor": action.actor,
        "reason": action.reason,
        "stage": action.stage,
        "workitem_id": action.workitem_id or "",
        "payload": dict(action.payload),
        "created_at": action.created_at,
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


def _bulk_claim_payload(
    state: SharedProjectState,
    assignments: list[TaskAssignment],
    service: TaskCenterService,
    requested_limit: int,
) -> dict[str, object]:
    return {
        "ok": True,
        "project_id": state.project.id,
        "requested_limit": requested_limit,
        "claimed_count": len(assignments),
        "summary": service.summary(state),
        "tasks": [
            _assignment_payload(state, assignment, service)["task"]
            for assignment in assignments
        ],
    }


def _bulk_expired_lease_release_payload(
    state: SharedProjectState,
    assignments: list[TaskAssignment],
    service: TaskCenterService,
) -> dict[str, object]:
    return {
        "ok": True,
        "project_id": state.project.id,
        "released_count": len(assignments),
        "summary": service.summary(state),
        "tasks": [
            _assignment_payload(state, assignment, service)["task"]
            for assignment in assignments
        ],
    }


def _sweep_payload(
    state: SharedProjectState,
    result,
    service: TaskCenterService,
    stale_after_seconds: int,
) -> dict[str, object]:
    released = [*result.expired_lease_assignments, *result.stale_assignments]
    return {
        "ok": True,
        "project_id": state.project.id,
        "released_count": len(released),
        "expired_lease_released_count": len(result.expired_lease_assignments),
        "stale_released_count": len(result.stale_assignments),
        "stale_after_seconds": stale_after_seconds,
        "summary": service.summary(state, stale_after_seconds=stale_after_seconds),
        "expired_lease_tasks": [
            _assignment_payload(state, assignment, service, stale_after_seconds=stale_after_seconds)["task"]
            for assignment in result.expired_lease_assignments
        ],
        "stale_tasks": [
            _assignment_payload(state, assignment, service, stale_after_seconds=stale_after_seconds)["task"]
            for assignment in result.stale_assignments
        ],
    }


def _sweep_all_payload(
    store: FileStateStore,
    service: TaskCenterService,
    *,
    stale_after_seconds: int,
    expired_lease_release_reason: str,
    stale_release_reason: str,
) -> dict[str, object]:
    project_results: list[dict[str, object]] = []
    total_expired = 0
    total_stale = 0
    for state in store.list_states():
        result = service.sweep(
            state.project.id,
            stale_after_seconds=stale_after_seconds,
            expired_lease_release_reason=expired_lease_release_reason,
            stale_release_reason=stale_release_reason,
        )
        expired_count = len(result.expired_lease_assignments)
        stale_count = len(result.stale_assignments)
        total_expired += expired_count
        total_stale += stale_count
        project_results.append(
            {
                "project_id": state.project.id,
                "released_count": expired_count + stale_count,
                "expired_lease_released_count": expired_count,
                "stale_released_count": stale_count,
                "summary": service.summary(result.state, stale_after_seconds=stale_after_seconds),
            }
        )
    return {
        "ok": True,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "project_count": len(project_results),
        "released_count": total_expired + total_stale,
        "expired_lease_released_count": total_expired,
        "stale_released_count": total_stale,
        "stale_after_seconds": stale_after_seconds,
        "projects": project_results,
    }


def _maintenance_payload(
    store: FileStateStore,
    service: TaskCenterService,
    *,
    stale_after_seconds: int,
    expired_lease_release_reason: str,
    stale_release_reason: str,
) -> dict[str, object]:
    sweep_payload = _sweep_all_payload(
        store,
        service,
        stale_after_seconds=stale_after_seconds,
        expired_lease_release_reason=expired_lease_release_reason,
        stale_release_reason=stale_release_reason,
    )
    audit_payload = _audit_all_payload(store, service, stale_after_seconds=stale_after_seconds)
    return {
        "ok": True,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "status": "clean" if audit_payload["passed"] else "needs_attention",
        "project_count": audit_payload["project_count"],
        "released_count": sweep_payload["released_count"],
        "expired_lease_released_count": sweep_payload["expired_lease_released_count"],
        "stale_released_count": sweep_payload["stale_released_count"],
        "finding_count": audit_payload["finding_count"],
        "error_count": audit_payload["error_count"],
        "warning_count": audit_payload["warning_count"],
        "stale_after_seconds": stale_after_seconds,
        "attention_project_ids": audit_payload.get("attention_project_ids", []),
        "finding_code_counts": audit_payload.get("finding_code_counts", {}),
        "recommendations": audit_payload.get("recommendations", []),
        "pending_retest_project_ids": audit_payload.get("pending_retest_project_ids", []),
        "pending_retest_scopes": audit_payload.get("pending_retest_scopes", {}),
        "human_control_project_ids": audit_payload.get("human_control_project_ids", []),
        "active_human_control_actions": audit_payload.get("active_human_control_actions", []),
        "sweep": sweep_payload,
        "audit": audit_payload,
    }


def _write_json_output(path_arg: str, project_root: str, payload: dict[str, object]) -> Path:
    output_path = _resolve_project_output_path(path_arg, project_root)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return output_path


def _resolve_project_output_path(path_arg: str, project_root: str) -> Path:
    path = Path(path_arg).expanduser()
    if path.is_absolute():
        return path.resolve()
    return (Path(project_root).expanduser().resolve() / path).resolve()


def _write_latest_maintenance_output(path_arg: str, project_root: str, payload: dict[str, object]) -> Path:
    latest = {
        "ok": bool(payload.get("ok", False)),
        "generated_at": str(payload.get("generated_at", "")),
        "status": str(payload.get("status", "")),
        "project_count": int(payload.get("project_count", 0)),
        "released_count": int(payload.get("released_count", 0)),
        "finding_count": int(payload.get("finding_count", 0)),
        "error_count": int(payload.get("error_count", 0)),
        "warning_count": int(payload.get("warning_count", 0)),
        "attention_project_ids": _json_list_payload(payload.get("attention_project_ids")),
        "finding_code_counts": _json_dict_payload(payload.get("finding_code_counts")),
        "recommendations": _json_list_payload(payload.get("recommendations")),
        "pending_retest_project_ids": _json_list_payload(payload.get("pending_retest_project_ids")),
        "pending_retest_scopes": _json_dict_payload(payload.get("pending_retest_scopes")),
        "human_control_project_ids": _json_list_payload(payload.get("human_control_project_ids")),
        "active_human_control_actions": _json_list_payload(payload.get("active_human_control_actions")),
        "operator_guidance": str(payload.get("operator_guidance", "")),
        "operator_commands": _json_list_payload(payload.get("operator_commands")),
        "report_path": str(payload.get("output_path", "")),
    }
    return _write_json_output(path_arg, project_root, latest)


def _maintenance_status_payload(
    latest_arg: str,
    project_root: str,
    *,
    max_age_seconds: int,
) -> dict[str, object]:
    latest_path = _resolve_project_output_path(latest_arg, project_root)
    fallback_operator_commands = _maintenance_operator_commands(
        project_root=project_root,
        latest_path=latest_arg,
        max_age_seconds=max_age_seconds,
    )
    if not latest_path.exists():
        return {
            "ok": False,
            "exists": False,
            "healthy": False,
            "latest_path": str(latest_path),
            "operator_guidance": _maintenance_operator_guidance(),
            "operator_commands": fallback_operator_commands,
            "error": "latest maintenance file not found",
        }
    try:
        latest = json.loads(latest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        return {
            "ok": False,
            "exists": True,
            "healthy": False,
            "latest_path": str(latest_path),
            "operator_guidance": _maintenance_operator_guidance(),
            "operator_commands": fallback_operator_commands,
            "error": f"latest maintenance file is not readable JSON: {error}",
        }
    if not isinstance(latest, dict):
        return {
            "ok": False,
            "exists": True,
            "healthy": False,
            "latest_path": str(latest_path),
            "operator_guidance": _maintenance_operator_guidance(),
            "operator_commands": fallback_operator_commands,
            "error": "latest maintenance file must contain a JSON object",
        }
    age_seconds = _maintenance_latest_age_seconds(str(latest.get("generated_at", "")))
    stale = max_age_seconds > 0 and (age_seconds is None or age_seconds > max_age_seconds)
    finding_count = int(latest.get("finding_count", 0))
    error_count = int(latest.get("error_count", 0))
    warning_count = int(latest.get("warning_count", 0))
    status = str(latest.get("status", ""))
    reason = _maintenance_health_reason(
        status=status,
        stale=stale,
        age_seconds=age_seconds,
        finding_count=finding_count,
        error_count=error_count,
        warning_count=warning_count,
    )
    healthy = status == "clean" and finding_count == 0 and error_count == 0 and warning_count == 0 and not stale
    operator_commands = _json_list_payload(latest.get("operator_commands")) or _maintenance_operator_commands(
        project_root=project_root,
        report_path=str(latest.get("report_path", "")) or ".conductor/maintenance/report.json",
        latest_path=latest_arg,
        max_age_seconds=max_age_seconds,
    )
    return {
        "ok": True,
        "exists": True,
        "healthy": healthy,
        "reason": reason,
        "stale": stale,
        "latest_path": str(latest_path),
        "max_age_seconds": max_age_seconds,
        "age_seconds": age_seconds,
        "status": status,
        "project_count": int(latest.get("project_count", 0)),
        "released_count": int(latest.get("released_count", 0)),
        "finding_count": finding_count,
        "error_count": error_count,
        "warning_count": warning_count,
        "attention_project_ids": _json_list_payload(latest.get("attention_project_ids")),
        "finding_code_counts": _json_dict_payload(latest.get("finding_code_counts")),
        "recommendations": _json_list_payload(latest.get("recommendations")),
        "pending_retest_project_ids": _json_list_payload(latest.get("pending_retest_project_ids")),
        "pending_retest_scopes": _json_dict_payload(latest.get("pending_retest_scopes")),
        "human_control_project_ids": _json_list_payload(latest.get("human_control_project_ids")),
        "active_human_control_actions": _json_list_payload(latest.get("active_human_control_actions")),
        "operator_guidance": str(latest.get("operator_guidance", "")) or _maintenance_operator_guidance(),
        "operator_commands": operator_commands,
        "report_path": str(latest.get("report_path", "")),
        "latest": latest,
    }


def _watchdog_payload(
    store: FileStateStore,
    service: TaskCenterService,
    *,
    project_root: str,
    latest_path: str,
    max_age_seconds: int,
    stale_after_seconds: int,
    expired_lease_release_reason: str,
    stale_release_reason: str,
    report_path: str,
    latest_output_path: str,
    check_only: bool,
) -> dict[str, object]:
    before = _maintenance_status_payload(latest_path, project_root, max_age_seconds=max_age_seconds)
    refresh_reason = _watchdog_refresh_reason(before)
    should_refresh = not check_only and refresh_reason != "healthy"
    maintenance_payload: dict[str, object] = {}
    after = before
    if should_refresh:
        maintenance_payload = _maintenance_payload(
            store,
            service,
            stale_after_seconds=stale_after_seconds,
            expired_lease_release_reason=expired_lease_release_reason,
            stale_release_reason=stale_release_reason,
        )
        _attach_maintenance_operator_hints(
            maintenance_payload,
            project_root=project_root,
            stale_after_seconds=stale_after_seconds,
            report_path=report_path,
            latest_path=latest_output_path,
            max_age_seconds=max_age_seconds,
        )
        output_path = _write_json_output(report_path, project_root, maintenance_payload)
        maintenance_payload["output_path"] = str(output_path)
        latest_output = _write_latest_maintenance_output(latest_output_path, project_root, maintenance_payload)
        maintenance_payload["latest_output_path"] = str(latest_output)
        after = _maintenance_status_payload(latest_output_path, project_root, max_age_seconds=max_age_seconds)
    return {
        "ok": bool(after.get("ok", False)),
        "healthy": bool(after.get("healthy", False)),
        "reason": _watchdog_status_reason(after),
        "maintenance_ran": should_refresh,
        "refresh_reason": refresh_reason,
        "check_only": check_only,
        "latest_path": str(_resolve_project_output_path(latest_path, project_root)),
        "report_path": str(_resolve_project_output_path(report_path, project_root)),
        "latest_output_path": str(_resolve_project_output_path(latest_output_path, project_root)),
        "pending_retest_project_ids": _json_list_payload(after.get("pending_retest_project_ids")),
        "pending_retest_scopes": _json_dict_payload(after.get("pending_retest_scopes")),
        "human_control_project_ids": _json_list_payload(after.get("human_control_project_ids")),
        "active_human_control_actions": _json_list_payload(after.get("active_human_control_actions")),
        "operator_guidance": _watchdog_operator_guidance(),
        "operator_commands": _watchdog_operator_commands(
            project_root=project_root,
            latest_path=latest_path,
            max_age_seconds=max_age_seconds,
            stale_after_seconds=stale_after_seconds,
            report_path=report_path,
            latest_output_path=latest_output_path,
        ),
        "before": before,
        "status": after,
        "maintenance": maintenance_payload,
    }


def _watchdog_refresh_reason(status_payload: dict[str, object]) -> str:
    if not status_payload.get("exists", False):
        return "missing_latest"
    if not status_payload.get("ok", False):
        return "invalid_latest"
    if status_payload.get("stale", False):
        return "stale_latest"
    if not status_payload.get("healthy", False):
        return str(status_payload.get("reason", "")) or "unhealthy_latest"
    return "healthy"


def _watchdog_status_reason(status_payload: dict[str, object]) -> str:
    reason = str(status_payload.get("reason", ""))
    if reason:
        return reason
    error = str(status_payload.get("error", ""))
    return error or "unknown"


def _watchdog_operator_guidance() -> str:
    return (
        "Run watchdog from a scheduler; it refreshes maintenance when the latest pointer "
        "is missing, stale, or unhealthy."
    )


def _watchdog_operator_commands(
    *,
    project_root: str,
    latest_path: str,
    max_age_seconds: int,
    stale_after_seconds: int,
    report_path: str,
    latest_output_path: str,
) -> list[str]:
    watchdog = [
        "python",
        "-m",
        "app.task_center",
        "watchdog",
        "--project-root",
        _quote_cli_arg(project_root),
        "--latest",
        _quote_cli_arg(latest_path),
        "--stale-after-seconds",
        str(stale_after_seconds),
        "--output",
        _quote_cli_arg(report_path),
        "--latest-output",
        _quote_cli_arg(latest_output_path),
    ]
    status = [
        "python",
        "-m",
        "app.task_center",
        "maintenance-status",
        "--project-root",
        _quote_cli_arg(project_root),
        "--latest",
        _quote_cli_arg(latest_output_path),
    ]
    if max_age_seconds > 0:
        watchdog.extend(["--max-age-seconds", str(max_age_seconds)])
        status.extend(["--max-age-seconds", str(max_age_seconds)])
    watchdog.append("--fail-on-unhealthy")
    status.append("--fail-on-findings")
    return [
        " ".join(watchdog),
        " ".join(status),
        _human_control_status_all_command(project_root),
    ]


def _attach_maintenance_operator_hints(
    payload: dict[str, object],
    *,
    project_root: str,
    stale_after_seconds: int = DEFAULT_STALE_CLAIMED_AFTER_SECONDS,
    report_path: str = ".conductor/maintenance/report.json",
    latest_path: str = ".conductor/maintenance/latest.json",
    max_age_seconds: int = 0,
) -> None:
    payload["operator_guidance"] = _maintenance_operator_guidance()
    payload["operator_commands"] = _maintenance_operator_commands(
        project_root=project_root,
        stale_after_seconds=stale_after_seconds,
        report_path=report_path,
        latest_path=latest_path,
        max_age_seconds=max_age_seconds,
    )


def _maintenance_operator_guidance() -> str:
    return (
        "Schedule the maintenance command, then have watchdogs call maintenance-status "
        "against the latest pointer."
    )


def _maintenance_operator_commands(
    *,
    project_root: str,
    stale_after_seconds: int = DEFAULT_STALE_CLAIMED_AFTER_SECONDS,
    report_path: str = ".conductor/maintenance/report.json",
    latest_path: str = ".conductor/maintenance/latest.json",
    max_age_seconds: int = 0,
) -> list[str]:
    maintenance = [
        "python",
        "-m",
        "app.task_center",
        "maintenance",
        "--project-root",
        _quote_cli_arg(project_root),
        "--stale-after-seconds",
        str(stale_after_seconds),
        "--output",
        _quote_cli_arg(report_path),
        "--latest-output",
        _quote_cli_arg(latest_path),
    ]
    status = [
        "python",
        "-m",
        "app.task_center",
        "maintenance-status",
        "--project-root",
        _quote_cli_arg(project_root),
        "--latest",
        _quote_cli_arg(latest_path),
    ]
    if max_age_seconds > 0:
        status.extend(["--max-age-seconds", str(max_age_seconds)])
    status.append("--fail-on-findings")
    return [
        " ".join(maintenance),
        " ".join(status),
        _human_control_status_all_command(project_root),
    ]


def _human_control_status_all_command(project_root: str) -> str:
    return " ".join(
        [
            "python",
            "-m",
            "app.human_control",
            "status-all",
            "--project-root",
            _quote_cli_arg(project_root),
            "--active-only",
            "--fail-on-active",
            "--output",
            _quote_cli_arg(".conductor/human-control/status.json"),
        ]
    )


def _quote_cli_arg(value: object) -> str:
    return '"' + str(value).replace('"', '\\"') + '"'


def _maintenance_health_reason(
    *,
    status: str,
    stale: bool,
    age_seconds: int | None,
    finding_count: int,
    error_count: int,
    warning_count: int,
) -> str:
    if stale:
        return "invalid_generated_at" if age_seconds is None else "stale"
    if finding_count > 0 or error_count > 0 or warning_count > 0:
        return "findings"
    if status != "clean":
        return "status_not_clean"
    return "clean"


def _maintenance_latest_age_seconds(generated_at: str) -> int | None:
    if not generated_at:
        return None
    try:
        parsed = datetime.fromisoformat(generated_at.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return max(0, int((datetime.now(timezone.utc) - parsed.astimezone(timezone.utc)).total_seconds()))


def _json_list_payload(value: object) -> list[object]:
    return value if isinstance(value, list) else []


def _json_dict_payload(value: object) -> dict[str, object]:
    return value if isinstance(value, dict) else {}


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
            "claim_token": assignment.claim_token,
            "blocked_reason": assignment.blocked_reason or "",
            "claimable": service.claimable(state, assignment),
            "unmet_dependency_ids": service.unmet_dependency_ids(state, assignment),
            "claimed_age_seconds": claimed_age_seconds,
            "heartbeat_age_seconds": heartbeat_age_seconds,
            "stale_claimed": service.stale_claimed(assignment, stale_after_seconds=stale_after_seconds),
            "lease_seconds": assignment.lease_seconds,
            "lease_expires_at": assignment.lease_expires_at,
            "lease_expired": service.lease_expired(assignment),
            "workitem": asdict(workitem) if workitem else {},
            "return_commands": _task_return_commands(state, assignment),
        },
    }


def _task_return_commands(state: SharedProjectState, assignment: TaskAssignment) -> dict[str, str]:
    if assignment.status != TaskAssignmentStatus.CLAIMED:
        return {}
    root = state.project.project_root or "<project-root>"
    agent_id = assignment.assigned_agent_id or "<agent-id>"
    claim_token = assignment.claim_token or "<claim-token>"
    base = [
        "python",
        "-m",
        "app.task_center",
    ]
    common = [
        _quote_cli_arg(assignment.id),
        "--project-root",
        _quote_cli_arg(root),
        "--agent-id",
        _quote_cli_arg(agent_id),
        "--claim-token",
        _quote_cli_arg(claim_token),
    ]
    return {
        "complete": " ".join([*base, "complete", *common, "--result-summary", _quote_cli_arg("done")]),
        "fail": " ".join([*base, "fail", *common, "--blocked-reason", _quote_cli_arg("blocked")]),
        "heartbeat": " ".join([*base, "heartbeat", *common]),
        "release": " ".join([*base, "release", *common, "--release-reason", _quote_cli_arg("release claim")]),
    }


if __name__ == "__main__":
    raise SystemExit(main())
