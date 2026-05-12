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
