"""Requirement-stage dynamic team planning tests."""

from conductor.collaboration.team import plan_requirement_review_team


def test_requirement_team_planner_expands_complex_project_roles() -> None:
    plan = plan_requirement_review_team(
        (
            "Build an expense approval web API with employee and manager roles, REST API, "
            "status workflow, validation errors, persisted data, CSV export, and audit permissions."
        ),
        peer_roles=["designer", "solution_designer"],
        functional_roles=["backend_engineer", "backend_engineer", "tester"],
    )

    peer_ids = {seat.seat_id for seat in plan.peer_seats}
    functional_ids = {seat.seat_id for seat in plan.functional_seats}

    assert plan.complexity_level == "complex"
    assert "designer.information_architecture" in peer_ids
    assert "designer.information_architecture" in peer_ids
    assert "solution_designer.process" in peer_ids
    assert "solution_designer.security_boundary" in peer_ids
    assert "backend_engineer.contracts" in functional_ids
    assert "backend_engineer.states" in functional_ids
    assert "tester.edge_cases" in functional_ids


def test_requirement_team_planner_keeps_simple_project_small() -> None:
    plan = plan_requirement_review_team(
        "Create a tiny local counter.",
        peer_roles=["designer", "solution_designer"],
        functional_roles=["tester"],
    )

    assert plan.complexity_level == "simple"
    assert [seat.seat_id for seat in plan.peer_seats] == ["designer", "solution_designer"]
    assert [seat.seat_id for seat in plan.functional_seats] == ["tester"]


def test_requirement_team_planner_does_not_treat_reading_status_as_workflow() -> None:
    plan = plan_requirement_review_team(
        "Reading list web app with reading status filter, CSV export, and persisted data.",
        peer_roles=["designer", "solution_designer"],
        functional_roles=["backend_engineer", "backend_engineer", "tester"],
    )

    peer_ids = {seat.seat_id for seat in plan.peer_seats}

    assert "solution_designer.process" not in peer_ids
