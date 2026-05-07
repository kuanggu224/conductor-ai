from dataclasses import replace

from fastapi.testclient import TestClient

from app import board
from conductor.agents.llm import LLMHTTPConfig
from conductor.config.llm import LLMRuntimeConfig, LLMUsagePolicy
from conductor.domain.models import Artifact


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


def test_diagnostics_api_returns_platform_health_snapshot(monkeypatch) -> None:
    monkeypatch.setattr(
        "conductor.diagnostics.discover_cli_tools",
        lambda: [type("Tool", (), {"name": "codex", "available": True})()],
    )
    client = TestClient(board.app)

    response = client.get("/api/diagnostics")

    assert response.status_code == 200
    payload = response.json()
    assert "ok" in payload
    assert "project_root" in payload
    assert "config_paths" in payload
    assert "available_cli_names" in payload
    assert "role_bindings" in payload
    assert "llm_backends" in payload
    assert payload["llm_backends"][0]["server_status"] == "not_checked"


def test_diagnostics_api_can_run_llm_preflight(monkeypatch) -> None:
    monkeypatch.setattr("conductor.diagnostics.discover_cli_tools", lambda: [])
    monkeypatch.setattr(
        board,
        "load_llm_runtime_config",
        lambda: LLMRuntimeConfig(
            local=LLMHTTPConfig(base_url="http://local.test/v1", model_name="local-model", enabled=False),
            cloud=LLMHTTPConfig(
                base_url="https://cloud.test/v1",
                model_name="cloud-model",
                api_key="secret",
                enabled=True,
            ),
            usage=LLMUsagePolicy(),
        ),
    )
    monkeypatch.setattr(
        board,
        "build_requirement_llm_preflight_probe",
        lambda *_: (lambda backend: (True, "")),
    )
    client = TestClient(board.app)

    response = client.get("/api/diagnostics?preflight_llm=true")

    assert response.status_code == 200
    cloud = {item["backend"]: item for item in response.json()["llm_backends"]}["cloud"]
    assert cloud["preflight_success"] is True


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


def test_project_tasks_summary_api_returns_counts() -> None:
    client = TestClient(board.app)
    state = board.engine.create_project(requirement="Build a local reading list", project_root="")

    response = client.get(f"/api/projects/{state.project.id}/tasks/summary")

    assert response.status_code == 200
    payload = response.json()
    assert payload["project_id"] == state.project.id
    assert payload["summary"]["total"] == 1
    assert payload["summary"]["claimable"] == 1
    assert payload["summary"]["blocked_by_dependencies"] == 0
    assert "tasks" not in payload


def test_project_task_claim_and_complete_protocol() -> None:
    client = TestClient(board.app)
    state = board.engine.create_project(requirement="Build a local reading list", project_root="")
    assignment_id = state.task_assignments[0].id

    claimed = client.post(
        f"/api/projects/{state.project.id}/tasks/{assignment_id}/claim",
        json={"agent_id": "agent-manual", "claim_reason": "manual smoke"},
    )

    assert claimed.status_code == 200
    claimed_payload = claimed.json()
    assert claimed_payload["summary"]["claimed"] == 1
    assert claimed_payload["summary"]["claimable"] == 0
    claimed_task = claimed_payload["task"]
    assert claimed_task["status"] == "claimed"
    assert claimed_task["assigned_agent_id"] == "agent-manual"
    assert claimed_task["claim_reason"] == "manual smoke"
    assert claimed_task["claimed_at"]
    assert claimed_task["returned_at"] == ""
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
    completed_payload = completed.json()
    assert completed_payload["summary"]["completed"] == 1
    assert completed_payload["summary"]["claimable"] == 0
    completed_task = completed_payload["task"]
    assert completed_task["status"] == "completed"
    assert completed_task["result_summary"] == "finished task"
    assert completed_task["output_artifact_ids"] == ["artifact-manual"]
    assert completed_task["returned_at"]
    assert completed_task["workitem"]["status"] == "done"

    completed_list = client.get(f"/api/projects/{state.project.id}/tasks?status=completed")
    assert completed_list.status_code == 200
    assert completed_list.json()["total"] == 1


def test_project_task_complete_api_can_create_output_artifact() -> None:
    client = TestClient(board.app)
    state = board.engine.create_project(requirement="Build a local reading list", project_root="")
    assignment_id = state.task_assignments[0].id
    claim = client.post(
        f"/api/projects/{state.project.id}/tasks/{assignment_id}/claim",
        json={"agent_id": "agent-api-worker"},
    )
    assert claim.status_code == 200

    completed = client.post(
        f"/api/projects/{state.project.id}/tasks/{assignment_id}/complete",
        json={
            "result_summary": "done",
            "output_artifact_content": "# Worker Result\n\nImplemented through API.",
            "output_artifact_kind": "implementation_report",
            "output_artifact_title": "API Worker Result",
        },
    )

    assert completed.status_code == 200
    artifact_id = completed.json()["task"]["output_artifact_ids"][0]
    reloaded = board.engine.get_project(state.project.id)
    artifact = next(item for item in reloaded.artifacts if item.id == artifact_id)
    assert artifact.kind == "implementation_report"
    assert artifact.title == "API Worker Result"
    assert artifact.source_backend == "task_center/external"
    assert "Implemented through API" in artifact.content


def test_project_task_complete_api_rejects_empty_output_artifact() -> None:
    client = TestClient(board.app)
    state = board.engine.create_project(requirement="Build a local reading list", project_root="")
    assignment_id = state.task_assignments[0].id
    claim = client.post(
        f"/api/projects/{state.project.id}/tasks/{assignment_id}/claim",
        json={"agent_id": "agent-api-worker"},
    )
    assert claim.status_code == 200

    response = client.post(
        f"/api/projects/{state.project.id}/tasks/{assignment_id}/complete",
        json={"output_artifact_content": "   "},
    )

    assert response.status_code == 422
    assert "cannot be empty" in response.json()["detail"]


def test_project_task_context_api_returns_input_artifact_content() -> None:
    client = TestClient(board.app)
    state = board.engine.create_project(
        requirement="Build a local reading list with CSV export",
        project_root="",
    )
    artifact = board.engine.artifact_store.save_markdown(
        Artifact(
            id="artifact-context",
            project_id=state.project.id,
            workitem_id="workitem-upstream",
            agent_id="agent-designer",
            kind="frozen_requirement_spec",
            title="Frozen Requirement",
            content="Acceptance: add book, persist refresh, export CSV.",
        ),
        project_root=state.project.project_root,
    )
    state = board.engine.state_store.add_artifact(state.project.id, artifact)
    assignment = replace(state.task_assignments[0], input_artifact_ids=[artifact.id])
    board.engine.state_store.upsert_task_assignment(state.project.id, assignment)

    response = client.get(f"/api/projects/{state.project.id}/tasks/{assignment.id}/context")

    assert response.status_code == 200
    payload = response.json()
    assert payload["assignment"]["id"] == assignment.id
    assert payload["assignment"]["input_artifact_ids"]
    assert payload["workitem"]["id"] == assignment.workitem_id
    assert "Return Protocol" in payload["execution_brief"]
    assert payload["input_artifacts"]
    assert "content" in payload["input_artifacts"][0]


def test_project_task_context_api_can_return_markdown_prompt() -> None:
    client = TestClient(board.app)
    state = board.engine.create_project(
        requirement="Build a local reading list with CSV export",
        project_root="",
    )
    artifact = board.engine.artifact_store.save_markdown(
        Artifact(
            id="artifact-context-markdown",
            project_id=state.project.id,
            workitem_id="workitem-upstream",
            agent_id="agent-designer",
            kind="frozen_requirement_spec",
            title="Frozen Requirement",
            content="Acceptance: add book, persist refresh, export CSV.",
        ),
        project_root=state.project.project_root,
    )
    state = board.engine.state_store.add_artifact(state.project.id, artifact)
    assignment = replace(state.task_assignments[0], input_artifact_ids=[artifact.id])
    board.engine.state_store.upsert_task_assignment(state.project.id, assignment)

    response = client.get(f"/api/projects/{state.project.id}/tasks/{assignment.id}/context?format=markdown")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/markdown")
    assert response.text.startswith("# Task Assignment Context")
    assert "artifact-context-markdown" in response.text
    assert "Acceptance: add book, persist refresh, export CSV." in response.text
    assert "## CLI Return Commands" in response.text
    assert f'python -m app.task_center complete "{assignment.id}"' in response.text


def test_project_task_claim_api_can_include_context() -> None:
    client = TestClient(board.app)
    state = board.engine.create_project(requirement="Build a local reading list with CSV export", project_root="")
    artifact = board.engine.artifact_store.save_markdown(
        Artifact(
            id="artifact-claim-context",
            project_id=state.project.id,
            workitem_id="workitem-upstream",
            agent_id="agent-designer",
            kind="frozen_requirement_spec",
            title="Frozen Requirement",
            content="Acceptance: add book, persist refresh, export CSV.",
        ),
        project_root=state.project.project_root,
    )
    state = board.engine.state_store.add_artifact(state.project.id, artifact)
    assignment = replace(state.task_assignments[0], input_artifact_ids=[artifact.id])
    board.engine.state_store.upsert_task_assignment(state.project.id, assignment)

    response = client.post(
        f"/api/projects/{state.project.id}/tasks/{assignment.id}/claim",
        json={"agent_id": "agent-api-worker", "include_context": True},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["task"]["status"] == "claimed"
    assert payload["context"]["assignment"]["id"] == assignment.id
    assert "Return Protocol" in payload["context"]["execution_brief"]
    assert payload["context"]["input_artifacts"][0]["id"] == artifact.id
    assert "content" in payload["context"]["input_artifacts"][0]


def test_project_task_claim_api_can_include_context_markdown() -> None:
    client = TestClient(board.app)
    state = board.engine.create_project(requirement="Build a local reading list with CSV export", project_root="")
    artifact = board.engine.artifact_store.save_markdown(
        Artifact(
            id="artifact-claim-context-markdown",
            project_id=state.project.id,
            workitem_id="workitem-upstream",
            agent_id="agent-designer",
            kind="frozen_requirement_spec",
            title="Frozen Requirement",
            content="Acceptance: add book, persist refresh, export CSV.",
        ),
        project_root=state.project.project_root,
    )
    state = board.engine.state_store.add_artifact(state.project.id, artifact)
    assignment = replace(state.task_assignments[0], input_artifact_ids=[artifact.id])
    board.engine.state_store.upsert_task_assignment(state.project.id, assignment)

    response = client.post(
        f"/api/projects/{state.project.id}/tasks/{assignment.id}/claim",
        json={
            "agent_id": "agent-api-worker",
            "include_context": True,
            "context_format": "markdown",
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["task"]["status"] == "claimed"
    assert "context" not in payload
    assert payload["context_markdown"].startswith("# Task Assignment Context")
    assert "artifact-claim-context-markdown" in payload["context_markdown"]
    assert f'python -m app.task_center complete "{assignment.id}"' in payload["context_markdown"]


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
    payload = response.json()
    assert payload["summary"]["claimed"] == 1
    assert payload["summary"]["claimable"] == 0
    task = payload["task"]
    assert task["role"] == role
    assert task["status"] == "claimed"
    assert task["assigned_agent_id"] == "agent-api-worker"
    assert task["claim_reason"] == "api worker"
    assert task["workitem"]["status"] == "running"


def test_project_task_claim_next_can_include_context_markdown() -> None:
    client = TestClient(board.app)
    state = board.engine.create_project(requirement="Build a local reading list with CSV export", project_root="")
    artifact = board.engine.artifact_store.save_markdown(
        Artifact(
            id="artifact-claim-next-context-markdown",
            project_id=state.project.id,
            workitem_id="workitem-upstream",
            agent_id="agent-designer",
            kind="frozen_requirement_spec",
            title="Frozen Requirement",
            content="Acceptance: add book, persist refresh, export CSV.",
        ),
        project_root=state.project.project_root,
    )
    state = board.engine.state_store.add_artifact(state.project.id, artifact)
    assignment = replace(state.task_assignments[0], input_artifact_ids=[artifact.id])
    board.engine.state_store.upsert_task_assignment(state.project.id, assignment)

    response = client.post(
        f"/api/projects/{state.project.id}/tasks/claim-next",
        json={
            "agent_id": "agent-api-worker",
            "include_context": True,
            "context_format": "markdown",
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["task"]["status"] == "claimed"
    assert "context" not in payload
    assert payload["context_markdown"].startswith("# Task Assignment Context")
    assert "artifact-claim-next-context-markdown" in payload["context_markdown"]
    assert f'python -m app.task_center complete "{assignment.id}"' in payload["context_markdown"]


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
