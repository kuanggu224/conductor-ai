from fastapi.testclient import TestClient

from app import board


def test_project_api_returns_snapshot_payload() -> None:
    client = TestClient(board.app)
    state = board.engine.create_project(
        requirement="实现一个包含 API、UI 和测试的最小系统",
        project_root=r"C:\99_self\conductor\conductor-front",
    )

    response = client.get(f"/api/projects/{state.project.id}")

    assert response.status_code == 200
    payload = response.json()
    assert payload["snapshot"]["project_id"] == state.project.id
    assert "design_collaboration" in payload["snapshot"]
    assert "execution_runtime" in payload["snapshot"]
    assert "task_status" in payload


def test_create_project_api_creates_project() -> None:
    client = TestClient(board.app)

    response = client.post(
        "/api/projects",
        json={
            "requirement": "实现一个包含 API、UI 和测试的最小系统",
            "project_root": r"C:\99_self\conductor\conductor-front",
        },
    )

    assert response.status_code == 201
    payload = response.json()
    assert payload["project_id"].startswith("project-")
    assert payload["snapshot"]["project_root"] == r"C:\99_self\conductor\conductor-front"


def test_create_project_api_accepts_utf8_requirement_file(tmp_path) -> None:
    client = TestClient(board.app)
    requirement_path = tmp_path / "requirement.txt"
    requirement_path.write_text("实现中文需求：费用报销审批 Web UI 和 API", encoding="utf-8")

    response = client.post(
        "/api/projects",
        json={
            "requirement_file": str(requirement_path),
            "project_root": str(tmp_path / "project"),
        },
    )

    assert response.status_code == 201
    assert response.json()["snapshot"]["project_goal"] == "实现中文需求：费用报销审批 Web UI 和 API"


def test_cli_settings_api_returns_discovered_tools() -> None:
    client = TestClient(board.app)

    response = client.get("/api/settings/cli")

    assert response.status_code == 200
    payload = response.json()
    assert "config" in payload
    assert "cli_options" in payload
    assert "role_cli_options" in payload


def test_project_detail_api_returns_404_for_missing_project() -> None:
    client = TestClient(board.app)

    response = client.get("/api/projects/project-missing")

    assert response.status_code == 404
    assert response.json()["detail"] == "Project not found: project-missing"


def test_project_tasks_api_returns_task_center_assignments() -> None:
    client = TestClient(board.app)
    state = board.engine.create_project(
        requirement="Build a local reading list with CSV export",
        project_root="",
    )

    response = client.get(f"/api/projects/{state.project.id}/tasks")

    assert response.status_code == 200
    payload = response.json()
    assert payload["project_id"] == state.project.id
    assert payload["total"] == 1
    assert payload["summary"]["total"] == 1
    assert payload["summary"]["claimable"] == 1
    assert payload["summary"]["blocked_by_dependencies"] == 0
    task = payload["tasks"][0]
    assert task["workitem_id"] == state.workitems[0].id
    assert task["status"] == "queued"
    assert task["claimable"] is True
    assert task["unmet_dependency_ids"] == []
    assert task["workitem"]["acceptance_criteria"] == state.workitems[0].acceptance_criteria
    assert "artifacts" in task


def test_project_tasks_api_filters_by_status() -> None:
    client = TestClient(board.app)
    state = board.engine.create_project(requirement="Build a local reading list", project_root="")

    queued = client.get(f"/api/projects/{state.project.id}/tasks?status=queued")
    completed = client.get(f"/api/projects/{state.project.id}/tasks?status=completed")

    assert queued.status_code == 200
    assert queued.json()["total"] == 1
    assert completed.status_code == 200
    assert completed.json()["total"] == 0


def test_project_task_claim_and_complete_protocol() -> None:
    client = TestClient(board.app)
    state = board.engine.create_project(requirement="Build a local reading list", project_root="")
    assignment_id = state.task_assignments[0].id

    claimed = client.post(
        f"/api/projects/{state.project.id}/tasks/{assignment_id}/claim",
        json={"agent_id": "agent-manual", "claim_reason": "manual smoke"},
    )

    assert claimed.status_code == 200
    claimed_task = claimed.json()["task"]
    assert claimed_task["status"] == "claimed"
    assert claimed_task["assigned_agent_id"] == "agent-manual"
    assert claimed_task["claim_reason"] == "manual smoke"
    assert claimed_task["workitem"]["status"] == "running"
    assert claimed_task["workitem"]["owner_agent"] == "agent-manual"

    claimed_list = client.get(f"/api/projects/{state.project.id}/tasks?status=claimed")
    assert claimed_list.status_code == 200
    assert claimed_list.json()["total"] == 1

    completed = client.post(
        f"/api/projects/{state.project.id}/tasks/{assignment_id}/complete",
        json={"result_summary": "finished task", "output_artifact_ids": ["artifact-manual"]},
    )

    assert completed.status_code == 200
    completed_task = completed.json()["task"]
    assert completed_task["status"] == "completed"
    assert completed_task["result_summary"] == "finished task"
    assert completed_task["output_artifact_ids"] == ["artifact-manual"]
    assert completed_task["workitem"]["status"] == "done"

    completed_list = client.get(f"/api/projects/{state.project.id}/tasks?status=completed")
    assert completed_list.status_code == 200
    assert completed_list.json()["total"] == 1


def test_project_task_claim_rejects_non_queued_assignment() -> None:
    client = TestClient(board.app)
    state = board.engine.create_project(requirement="Build a local reading list", project_root="")
    assignment_id = state.task_assignments[0].id

    first_claim = client.post(
        f"/api/projects/{state.project.id}/tasks/{assignment_id}/claim",
        json={"agent_id": "agent-manual"},
    )
    second_claim = client.post(
        f"/api/projects/{state.project.id}/tasks/{assignment_id}/claim",
        json={"agent_id": "agent-other"},
    )

    assert first_claim.status_code == 200
    assert second_claim.status_code == 409


def test_project_task_claim_next_selects_available_role_task() -> None:
    client = TestClient(board.app)
    state = board.engine.create_project(requirement="Build a local reading list", project_root="")
    role = state.task_assignments[0].role

    response = client.post(
        f"/api/projects/{state.project.id}/tasks/claim-next",
        json={"agent_id": "agent-api-worker", "role": role, "claim_reason": "api worker"},
    )

    assert response.status_code == 200
    task = response.json()["task"]
    assert task["role"] == role
    assert task["status"] == "claimed"
    assert task["assigned_agent_id"] == "agent-api-worker"
    assert task["claim_reason"] == "api worker"
    assert task["workitem"]["status"] == "running"


def test_project_task_claim_next_returns_404_when_no_role_task() -> None:
    client = TestClient(board.app)
    state = board.engine.create_project(requirement="Build a local reading list", project_root="")

    response = client.post(
        f"/api/projects/{state.project.id}/tasks/claim-next",
        json={"agent_id": "agent-api-worker", "role": "missing_role"},
    )

    assert response.status_code == 404
    assert response.json()["detail"] == "No queued task assignment available for role missing_role."


def test_project_task_return_missing_assignment_404() -> None:
    client = TestClient(board.app)
    state = board.engine.create_project(requirement="Build a local reading list", project_root="")

    response = client.post(
        f"/api/projects/{state.project.id}/tasks/task-assignment-missing/complete",
        json={"result_summary": "done"},
    )

    assert response.status_code == 404
