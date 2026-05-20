"""Human takeover controls for project execution."""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timezone
from uuid import uuid4

from conductor.domain.models import HumanControlAction, HumanControlActionType, SharedProjectState
from conductor.state.store import InMemoryStateStore


class HumanControlService:
    """Append human control actions and expose controller hold checks."""

    def __init__(self, state_store: InMemoryStateStore, event_prefix: str = "HumanControl") -> None:
        self.state_store = state_store
        self.event_prefix = event_prefix

    def pause(self, project_id: str, actor: str = "human", reason: str = "") -> SharedProjectState:
        """Pause automatic controller advancement until a resume action is recorded."""
        return self._append(project_id, HumanControlActionType.PAUSE, actor=actor, reason=reason)

    def resume(self, project_id: str, actor: str = "human", reason: str = "") -> SharedProjectState:
        """Resume automatic controller advancement after a pause or approval hold."""
        return self._append(project_id, HumanControlActionType.RESUME, actor=actor, reason=reason)

    def request_approval(
        self,
        project_id: str,
        actor: str = "tl_agent",
        reason: str = "",
        workitem_id: str | None = None,
        payload: dict[str, object] | None = None,
    ) -> SharedProjectState:
        """Hold the project until a human approves or rejects the pending decision."""
        return self._append(
            project_id,
            HumanControlActionType.REQUEST_APPROVAL,
            actor=actor,
            reason=reason,
            workitem_id=workitem_id,
            payload=payload or {},
        )

    def approve(
        self,
        project_id: str,
        actor: str = "human",
        reason: str = "",
        payload: dict[str, object] | None = None,
    ) -> SharedProjectState:
        """Approve a pending human gate and allow the controller to continue."""
        return self._append(
            project_id,
            HumanControlActionType.APPROVE,
            actor=actor,
            reason=reason,
            payload=payload or {},
        )

    def reject(self, project_id: str, actor: str = "human", reason: str = "") -> SharedProjectState:
        """Reject a pending human gate and keep the project on hold."""
        return self._append(project_id, HumanControlActionType.REJECT, actor=actor, reason=reason)

    def override(
        self,
        project_id: str,
        actor: str = "human",
        reason: str = "",
        payload: dict[str, object] | None = None,
    ) -> SharedProjectState:
        """Record a human override without encoding feature-specific policy here."""
        return self._append(
            project_id,
            HumanControlActionType.OVERRIDE,
            actor=actor,
            reason=reason,
            payload=payload or {},
        )

    def controller_hold_reason(self, state: SharedProjectState) -> str | None:
        """Return why automatic advancement must stop, if a human gate is active."""
        active = self.active_action(state)
        if active is None:
            return None
        if active.action == HumanControlActionType.PAUSE:
            return f"human_paused: {active.reason}".strip()
        if active.action == HumanControlActionType.REQUEST_APPROVAL:
            return f"human_approval_required: {active.reason}".strip()
        if active.action == HumanControlActionType.REJECT:
            return f"human_rejected: {active.reason}".strip()
        return None

    def has_clearance(self, state: SharedProjectState, controller_action: str, stage: str) -> bool:
        """Return whether a human approved or overrode the latest matching TL gate."""
        expected = {"controller_action": controller_action, "stage": stage}
        for action in reversed(state.human_control_actions):
            payload = dict(action.payload)
            if not all(payload.get(key) == value for key, value in expected.items()):
                continue
            if action.action in {HumanControlActionType.APPROVE, HumanControlActionType.OVERRIDE}:
                return True
            if action.action == HumanControlActionType.REQUEST_APPROVAL:
                return False
        return False

    def available_actions(self, state: SharedProjectState) -> list[str]:
        """Return operator actions that make sense for the current control state."""
        active = self.active_action(state)
        if active is None:
            return ["pause", "request_approval"]
        if active.action == HumanControlActionType.PAUSE:
            return ["resume", "override"]
        if active.action == HumanControlActionType.REQUEST_APPROVAL:
            return ["approve", "reject", "override"]
        if active.action == HumanControlActionType.REJECT:
            return ["override", "resume"]
        return ["pause", "request_approval"]

    def operator_guidance(self, state: SharedProjectState) -> str:
        """Return a compact next-step hint for CLI and Board operators."""
        active = self.active_action(state)
        if active is None:
            return "Automation is not held. Use pause to inspect, or request_approval to create a gate."
        if active.action == HumanControlActionType.PAUSE:
            return "Project is paused. Use resume to continue, or override to record a manual decision."
        if active.action == HumanControlActionType.REQUEST_APPROVAL:
            return "Approval is pending. Use approve, reject, or override after reviewing the gate."
        if active.action == HumanControlActionType.REJECT:
            return "Gate was rejected. Use override or resume when the operator decides how to proceed."
        return "Review human-control history before continuing."

    def operator_command_templates(
        self,
        state: SharedProjectState,
        *,
        project_root: str | None = None,
    ) -> list[str]:
        """Return copyable CLI command templates for the current human-control state."""
        root = project_root or state.project.project_root or "<project-root>"
        active = self.active_action(state)
        active_payload = dict(active.payload) if active and active.payload else {}
        return [
            self._operator_command_template(state, action, root, active_payload)
            for action in self.available_actions(state)
        ]

    def active_action(self, state: SharedProjectState) -> HumanControlAction | None:
        """Return the latest active hold action, or None if control is released."""
        active: HumanControlAction | None = None
        for action in state.human_control_actions:
            if action.action in {
                HumanControlActionType.PAUSE,
                HumanControlActionType.REQUEST_APPROVAL,
                HumanControlActionType.REJECT,
            }:
                active = action
                continue
            if action.action in {
                HumanControlActionType.RESUME,
                HumanControlActionType.APPROVE,
                HumanControlActionType.OVERRIDE,
            }:
                active = None
        return active

    def _operator_command_template(
        self,
        state: SharedProjectState,
        action: str,
        project_root: str,
        active_payload: dict[str, object],
    ) -> str:
        cli_action = action.replace("_", "-")
        reason = {
            "pause": "inspect delivery",
            "request_approval": "requires approval",
            "resume": "continue",
            "approve": "approved after review",
            "reject": "needs correction",
            "override": "manual override",
        }.get(action, "operator decision")
        parts = [
            "python",
            "-m",
            "app.human_control",
            cli_action,
            "--project-root",
            self._quote_cli_arg(project_root),
            "--project-id",
            state.project.id,
            "--actor",
            "operator",
            "--reason",
            self._quote_cli_arg(reason),
        ]
        payload = active_payload if action in {"approve", "override"} else {}
        if action == "request_approval":
            payload = {"controller_action": "<controller-action>", "stage": state.current_stage or "<stage>"}
        controller_action = str(payload.get("controller_action", ""))
        stage = str(payload.get("stage", ""))
        if controller_action:
            parts.extend(["--controller-action", self._quote_cli_arg(controller_action)])
        if stage:
            parts.extend(["--stage", self._quote_cli_arg(stage)])
        return " ".join(parts)

    def _quote_cli_arg(self, value: object) -> str:
        return '"' + str(value).replace('"', '\\"') + '"'

    def _append(
        self,
        project_id: str,
        action: HumanControlActionType,
        *,
        actor: str,
        reason: str,
        workitem_id: str | None = None,
        payload: dict[str, object] | None = None,
    ) -> SharedProjectState:
        state = self.state_store.get_state(project_id)
        record = HumanControlAction(
            id=f"human-{uuid4().hex[:8]}",
            project_id=project_id,
            action=action,
            actor=actor,
            reason=reason,
            stage=state.current_stage or "",
            workitem_id=workitem_id,
            payload=payload or {},
            created_at=datetime.now(timezone.utc).isoformat(),
        )
        updated = replace(state, human_control_actions=[*state.human_control_actions, record])
        self.state_store.save_state(updated)
        self.state_store.add_event(project_id, f"{self.event_prefix}: {action.value} by {actor}")
        return self.state_store.get_state(project_id)


__all__ = ["HumanControlService"]
