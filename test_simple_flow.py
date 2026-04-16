#!/usr/bin/env python3
"""Simple project flow smoke test."""

from conductor.controller.engine import ConductorEngine


def test_simple_project_flow():
    """Smoke test for creating and stepping through a simple project."""
    print("=== Conductor project flow smoke test ===")

    engine = ConductorEngine()
    print("Engine created successfully")

    requirement = "Implement a simple to-do application"
    state = engine.create_project(requirement=requirement)
    print(f"Project created: {state.project.id}")
    print(f"   Goal: {state.project.goal}")
    print(f"   Current stage: {state.current_stage}")
    print(f"   Planned roles: {', '.join(state.planned_roles)}")

    print("\n=== Starting project flow ===")
    while not engine.is_terminal(state):
        print(f"Current stage: {state.current_stage}")
        print(f"   Work items: {len([w for w in state.workitems if w.stage == state.current_stage])}")
        print(
            f"   Completed: {len([w for w in state.workitems if w.stage == state.current_stage and w.status == 'done'])}"
        )
        state = engine.step_project(state.project.id)

    print("\n=== Project flow complete ===")
    print(f"Project status: {state.project_status}")
    print(f"Event log size: {len(state.recent_events)}")

    if state.blockers:
        print("Blockers:")
        for blocker in state.blockers:
            print(f"   - {blocker}")
    else:
        print("No blockers")

    print(f"Executions: {len(state.executions)}")
    print(f"Route decisions: {len(state.route_decisions)}")

    print("\n=== Event log ===")
    for event in state.recent_events:
        print(f"  - {event}")

    return state


if __name__ == "__main__":
    try:
        test_simple_project_flow()
    except Exception as exc:
        print(f"\nError: {exc}")
        import traceback

        print(traceback.format_exc())
