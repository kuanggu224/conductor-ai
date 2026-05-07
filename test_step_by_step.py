#!/usr/bin/env python3
"""Step-by-step project flow smoke test."""

from conductor.config.cli import CLISelectionConfig
from conductor.controller.engine import ConductorEngine


def run_step_by_step():
    """Smoke test that steps through a project a few times."""
    print("=== Step-by-step project flow smoke test ===")
    try:
        engine = ConductorEngine(cli_selection_config=CLISelectionConfig())
        requirement = "Implement a simple to-do application"
        state = engine.create_project(requirement=requirement)
        print("Project created successfully")

        for index in range(3):
            print(f"\n=== Step {index + 1} ===")
            if engine.is_terminal(state):
                print("Project already terminal")
                break

            state = engine.step_project(state.project.id)
            print(f"   Current stage: {state.current_stage}")
            print(f"   Work items: {len([w for w in state.workitems if w.stage == state.current_stage])}")
            print("   Status counts:")
            status_counts = {}
            for workitem in state.workitems:
                if workitem.stage == state.current_stage:
                    status_counts[workitem.status] = status_counts.get(workitem.status, 0) + 1
            for status, count in status_counts.items():
                print(f"      {status}: {count}")

        print("\n=== Result ===")
        print(f"Project status: {state.project_status}")
        print(f"Event log size: {len(state.recent_events)}")

        if state.executions:
            print("\nExecutions:")
            for execution in state.executions:
                print(f"   - {execution.workitem_id} / {execution.agent_id} / {execution.status}")

        if state.route_decisions:
            print("\nRoute decisions:")
            for decision in state.route_decisions:
                print(f"   - {decision.workitem_id} -> {decision.selected_agent}")

        print("\nFirst 10 events:")
        for index, event in enumerate(state.recent_events[:10], 1):
            print(f"   {index}. {event}")

        print("\nStep-by-step smoke test passed")
        return state
    except Exception as exc:
        print(f"\nStep-by-step flow failed: {exc}")
        import traceback

        print(traceback.format_exc())
        return None


def test_step_by_step() -> None:
    state = run_step_by_step()
    assert state is not None


if __name__ == "__main__":
    state = run_step_by_step()
    if state and state.blockers:
        print("\nBlockers:")
        for blocker in state.blockers:
            print(f"   - {blocker}")
