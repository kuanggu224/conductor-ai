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
