
"""Test Conductor system directly from Python."""

from conductor.config.cli import CLISelectionConfig
from conductor.controller.engine import ConductorEngine


def test_system():
    """Test the Conductor system with a simple requirement."""
    print("=== Conductor System Test ===")

    try:
        engine = ConductorEngine(cli_selection_config=CLISelectionConfig())
        print("Engine initialized successfully")

        requirement = "实现一个简单的待办事项应用，包含添加、删除和查询功能"
        state = engine.create_project(requirement=requirement)
        print(f"Project created: {state.project.id}")
        print(f"Project goal: {state.project.goal}")
        print(f"Current stage: {state.current_stage}")
        print(f"Planned roles: {state.planned_roles}")

        # Step through project
        print("\n=== Stepping through project ===")
        step_count = 0
        max_steps = 10

        while not engine.is_terminal(state) and step_count < max_steps:
            print(f"Step {step_count + 1} - Current stage: {state.current_stage}")
            state = engine.step_project(state.project.id)
            step_count += 1

            # Print events
            if state.recent_events:
                print("Events:")
                for event in state.recent_events[-3:]:
                    print(f"  - {event}")

        print(f"\nProject completed in {step_count} steps")

        if state.blockers:
            print("\nBlockers:")
            for blocker in state.blockers:
                print(f"  - {blocker}")

        # Print workitems
        print(f"\nWorkitems created: {len(state.workitems)}")

    except Exception as e:
        print(f"Error: {e}")
        import traceback
        print(f"Stack trace: {traceback.format_exc()}")


if __name__ == "__main__":
    test_system()
