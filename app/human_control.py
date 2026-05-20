"""CLI for human takeover and approval controls."""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict
from pathlib import Path

from conductor.control.human import HumanControlService
from conductor.domain.models import HumanControlAction, SharedProjectState
from conductor.io.encoding import configure_utf8_stdio
from conductor.state.file_store import FileStateStore

configure_utf8_stdio()


def build_parser() -> argparse.ArgumentParser:
    """Build the human-control command parser."""
    parser = argparse.ArgumentParser(prog="python -m app.human_control")
    subparsers = parser.add_subparsers(dest="command", required=True)

    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--project-root", default=str(Path.cwd()), help="Project root containing .conductor/state.")
    common.add_argument("--state-dir", help="Explicit state directory. Overrides --project-root.")
    common.add_argument("--project-id", help="Project id. Optional when the state directory contains one project.")

    subparsers.add_parser("status", parents=[common], help="Print the active human-control state.")

    pause = subparsers.add_parser("pause", parents=[common], help="Pause automatic controller advancement.")
    pause.add_argument("--actor", default="human")
    pause.add_argument("--reason", default="")

    resume = subparsers.add_parser("resume", parents=[common], help="Resume automatic controller advancement.")
    resume.add_argument("--actor", default="human")
    resume.add_argument("--reason", default="")

    request = subparsers.add_parser("request-approval", parents=[common], help="Request human approval for a gate.")
    request.add_argument("--actor", default="human")
    request.add_argument("--reason", default="")
    request.add_argument("--workitem-id")
    _add_gate_payload_args(request)

    approve = subparsers.add_parser("approve", parents=[common], help="Approve the active human gate.")
    approve.add_argument("--actor", default="human")
    approve.add_argument("--reason", default="")
    _add_gate_payload_args(approve)

    reject = subparsers.add_parser("reject", parents=[common], help="Reject the active human gate.")
    reject.add_argument("--actor", default="human")
    reject.add_argument("--reason", default="")

    override = subparsers.add_parser("override", parents=[common], help="Record a human override.")
    override.add_argument("--actor", default="human")
    override.add_argument("--reason", default="")
    _add_gate_payload_args(override)

    return parser


def main(argv: list[str] | None = None) -> int:
    """Run the human-control CLI and print JSON."""
    args = build_parser().parse_args(argv)
    try:
        store = FileStateStore(_resolve_state_dir(args))
        state = _resolve_state(store, args.project_id)
        service = HumanControlService(store)

        if args.command == "status":
            payload = _status_payload(state, service)
        elif args.command == "pause":
            payload = _status_payload(service.pause(state.project.id, actor=args.actor, reason=args.reason), service)
        elif args.command == "resume":
            payload = _status_payload(service.resume(state.project.id, actor=args.actor, reason=args.reason), service)
        elif args.command == "request-approval":
            request_payload = _explicit_gate_payload(args, default_stage=state.current_stage or "")
            payload = _status_payload(
                service.request_approval(
                    state.project.id,
                    actor=args.actor,
                    reason=args.reason,
                    workitem_id=args.workitem_id,
                    payload=request_payload,
                ),
                service,
            )
        elif args.command == "approve":
            payload = _status_payload(
                service.approve(
                    state.project.id,
                    actor=args.actor,
                    reason=args.reason,
                    payload=_gate_payload_or_active(args, state, service),
                ),
                service,
            )
        elif args.command == "reject":
            payload = _status_payload(service.reject(state.project.id, actor=args.actor, reason=args.reason), service)
        elif args.command == "override":
            payload = _status_payload(
                service.override(
                    state.project.id,
                    actor=args.actor,
                    reason=args.reason,
                    payload=_gate_payload_or_active(args, state, service),
                ),
                service,
            )
        else:
            raise ValueError(f"Unsupported command: {args.command}")
    except Exception as error:
        print(str(error), file=sys.stderr)
        return 2

    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0


def _add_gate_payload_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--controller-action", default="", help="Controller action this approval applies to.")
    parser.add_argument("--stage", default="", help="Workflow stage this approval applies to.")


def _resolve_state_dir(args) -> Path:
    if args.state_dir:
        return Path(args.state_dir)
    return Path(args.project_root) / ".conductor" / "state"


def _resolve_state(store: FileStateStore, project_id: str | None) -> SharedProjectState:
    if project_id:
        return store.get_state(project_id)
    states = store.list_states()
    if len(states) == 1:
        return states[0]
    if not states:
        raise ValueError("No project state found. Pass --project-id after creating a project.")
    ids = ", ".join(state.project.id for state in states)
    raise ValueError(f"Multiple project states found. Pass --project-id. Available: {ids}")


def _explicit_gate_payload(args, *, default_stage: str) -> dict[str, object]:
    payload: dict[str, object] = {}
    if getattr(args, "controller_action", ""):
        payload["controller_action"] = args.controller_action
    if getattr(args, "stage", ""):
        payload["stage"] = args.stage
    elif payload:
        payload["stage"] = default_stage
    return payload


def _gate_payload_or_active(args, state: SharedProjectState, service: HumanControlService) -> dict[str, object]:
    explicit = _explicit_gate_payload(args, default_stage=state.current_stage or "")
    if explicit:
        return explicit
    active = service.active_action(state)
    if active and active.payload:
        return dict(active.payload)
    return {}


def _status_payload(state: SharedProjectState, service: HumanControlService) -> dict[str, object]:
    active = service.active_action(state)
    return {
        "ok": True,
        "project_id": state.project.id,
        "project_status": state.project_status.value,
        "current_stage": state.current_stage or "",
        "active": active is not None,
        "hold_reason": service.controller_hold_reason(state) or "",
        "active_action": _action_payload(active) if active else {},
        "available_actions": service.available_actions(state),
        "operator_guidance": service.operator_guidance(state),
        "operator_commands": service.operator_command_templates(state),
        "action_count": len(state.human_control_actions),
        "actions": [_action_payload(action) for action in state.human_control_actions],
    }


def _action_payload(action: HumanControlAction | None) -> dict[str, object]:
    if action is None:
        return {}
    return asdict(action) | {"action": action.action.value}


if __name__ == "__main__":
    raise SystemExit(main())
