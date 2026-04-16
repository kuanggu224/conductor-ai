#!/usr/bin/env python3
"""Project initialization smoke test."""

from conductor.controller.engine import ConductorEngine


def test_initialization():
    """Smoke test for project initialization."""
    print("=== Project initialization smoke test ===")
    try:
        engine = ConductorEngine()
        print("1. Engine created successfully")

        requirement = "Implement a simple to-do application"
        state = engine.create_project(requirement=requirement)
        print("2. Project created successfully")
        print(f"   Project ID: {state.project.id}")
        print(f"   Goal: {state.project.goal}")
        print(f"   Current stage: {state.current_stage}")
        print(f"   Work item count: {len(state.workitems)}")
        print(f"   Planned roles: {', '.join(state.planned_roles)}")

        print("\nWork items:")
        for workitem in state.workitems:
            print(f"   - {workitem.id}: {workitem.description} (kind: {workitem.kind})")

        print("\nInitialization smoke test passed")
        return True
    except Exception as exc:
        print(f"\nInitialization failed: {exc}")
        import traceback

        print(traceback.format_exc())
        return False


if __name__ == "__main__":
    test_initialization()
