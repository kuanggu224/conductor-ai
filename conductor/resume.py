"""Resume cursor helpers for long-running project reliability."""

from __future__ import annotations

from conductor.domain.models import HumanControlAction, SharedProjectState


def build_resume_cursor(state: SharedProjectState) -> dict[str, object]:
    """Return a compact cursor for resuming controller-driven execution."""
    active_human_control = active_human_control_action_record(state)
    current_stage = state.current_stage or ""
    stage_workitems = [item for item in state.workitems if item.stage == current_stage] if current_stage else []
    pending = [item for item in stage_workitems if item.status.value == "pending"]
    running = [item for item in stage_workitems if item.status.value == "running"]
    retryable_failed = [
        item
        for item in stage_workitems
        if item.status.value == "failed" and item.retryable and item.retry_count < item.max_retries
    ]
    terminal_failed = [
        item
        for item in stage_workitems
        if item.status.value == "failed" and (not item.retryable or item.retry_count >= item.max_retries)
    ]
    if active_human_control:
        next_action = "human_hold"
    elif state.project_status.value == "completed":
        next_action = "complete"
    elif state.project_status.value == "blocked" or state.blockers or terminal_failed:
        next_action = "blocked"
    elif running:
        next_action = "inspect_running"
    elif pending:
        next_action = "execute_pending"
    elif retryable_failed:
        next_action = "retry_failed"
    else:
        next_action = "advance_or_wait"
    return {
        "project_id": state.project.id,
        "project_status": state.project_status.value,
        "current_stage": current_stage,
        "next_action": next_action,
        "terminal": state.project_status.value in {"completed", "blocked"},
        "blocked": bool(state.blockers or terminal_failed),
        "blockers": list(state.blockers),
        "next_pending_workitem_ids": [item.id for item in pending],
        "running_workitem_ids": [item.id for item in running],
        "retryable_failed_workitem_ids": [item.id for item in retryable_failed],
        "terminal_failed_workitem_ids": [item.id for item in terminal_failed],
        "completed_workitem_ids": [item.id for item in state.workitems if item.status.value == "done"],
        "last_execution_workitem_id": state.executions[-1].workitem_id if state.executions else "",
        "last_event": state.recent_events[-1] if state.recent_events else "",
        "active_human_control_action": active_human_control,
    }


def active_human_control_action_record(state: SharedProjectState) -> dict[str, object]:
    """Return the active human hold action for resume tooling."""
    active = None
    for action in state.human_control_actions:
        if action.action.value in {"pause", "request_approval", "reject"}:
            active = action
            continue
        if action.action.value in {"resume", "approve", "override"}:
            active = None
    return human_control_action_record(active) if active else {}


def human_control_action_record(action: HumanControlAction) -> dict[str, object]:
    """Return a JSON-safe human-control record."""
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
