"""Minimal test script for ConductorEngine."""

from conductor.controller.engine import ConductorEngine


def run_minimal_test():
    """Run a minimal test of the Conductor system."""
    print("=== Conductor Minimal Test ===")
    try:
        engine = ConductorEngine()
        print("Engine initialized successfully")

        requirement = "实现一个简单的待办事项应用，包含添加、删除和查询功能"
        state = engine.create_project(requirement=requirement)
        print(f"Project created: {state.project.id}")
        print(f"Project goal: {state.project.goal}")
        print(f"Current stage: {state.current_stage}")
        print(f"Planned roles: {state.planned_roles}")
        print(f"Workitems created: {len(state.workitems)}")

        # Step through project
        print("\n=== Stepping through project ===")
        step_count = 0
        max_steps = 5

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

    except Exception as e:
        print(f"Error: {e}")
        import traceback
        print(f"Stack trace: {traceback.format_exc()}")


if __name__ == "__main__":
    run_minimal_test()
