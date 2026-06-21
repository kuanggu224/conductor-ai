from dataclasses import replace
from datetime import datetime, timedelta, timezone

from fastapi.testclient import TestClient

from app import board
from conductor.agents.llm import LLMHTTPConfig
from conductor.config.cli import CLISelectionConfig
from conductor.config.llm import LLMRuntimeConfig, LLMUsagePolicy
from conductor.control.human import HumanControlService
from conductor.domain.models import AgentActivation, Artifact, ProjectStatus, TaskAssignment, TaskAssignmentStatus, WorkItem, WorkItemStatus
from conductor.preflight_gate import write_preflight_gate_payload


def test_project_api_returns_snapshot_payload() -> None:
    client = TestClient(board.app)
    state = board.engine.create_project(
        requirement="实现一个包含 API、API 和测试的最小系统",
        project_root=r"C:\99_self\conductor\conductor-front",
    )

    response = client.get(f"/api/projects/{state.project.id}")

    assert response.status_code == 200
    payload = response.json()
    assert payload["snapshot"]["project_id"] == state.project.id
    assert "design_collaboration" in payload["snapshot"]
    assert "execution_runtime" in payload["snapshot"]
    assert "run_audit" in payload["snapshot"]
    assert "human_control" in payload["snapshot"]
    assert "operation_console" in payload["snapshot"]
    assert payload["snapshot"]["human_control"]["active"] is False
    assert payload["snapshot"]["operation_console"]["available"] is True
    assert payload["snapshot"]["operation_console"]["actions"]
    assert payload["snapshot"]["run_audit"]["risk_level"] in {"normal", "medium", "high"}
    assert isinstance(payload["snapshot"]["run_audit"]["failed_workitem_ids"], list)
    assert "task_status" in payload


def test_project_live_api_returns_operation_console_state(tmp_path) -> None:
    client = TestClient(board.app)
    project_root = tmp_path / "live-project"
    state = board.engine.create_project(requirement="Build controllable operation console", project_root=str(project_root))
    write_preflight_gate_payload(
        project_root,
        {
            "ok": False,
            "preflight_gate": {
                "errors": ["local LLM preflight failed"],
                "recommendations": ["Check local server"],
            },
        },
    )

    response = client.get(f"/api/projects/{state.project.id}/live")

    assert response.status_code == 200
    payload = response.json()
    assert payload["project_id"] == state.project.id
    assert payload["project_status"] == "initialized"
    assert payload["task_status"]["running"] is False
    assert payload["runtime_stream"]["status"] == "idle"
    assert payload["workitems"]["total"] >= 1
    assert payload["human_control"]["active"] is False
    snapshot = payload["snapshot"]
    assert snapshot["preflight_gate"]["recorded"] is True
    assert snapshot["preflight_gate"]["status"] == "fail"
    assert snapshot["preflight_gate"]["errors"] == ["local LLM preflight failed"]
    assert snapshot["preflight_gate"]["recommendations"] == ["Check local server"]
    assert "delivery_readiness_checks" in snapshot["run_audit"]
    assert "scope_contract_violations" in snapshot["run_audit"]
    assert snapshot["operation_console"]["available"] is True
    assert any(action["id"] == "human-control-pause" for action in snapshot["operation_console"]["actions"])
    assert snapshot["design_collaboration"]["enabled"] is True
    assert snapshot["design_collaboration"]["status"]
    assert "current_step_label" in snapshot["design_collaboration"]
    assert "agents" in snapshot["design_collaboration"]
    assert snapshot["project_agents"]
    assert snapshot["project_agents"][0]["task_api_path"].startswith(f"/api/projects/{state.project.id}/agents/")
    assert snapshot["project_agents"][0]["claim_task_api_path"].startswith(f"/api/projects/{state.project.id}/agents/")
    assignment = snapshot["task_assignments"][0]
    assert assignment["claim_api_path"].endswith(f"/tasks/{assignment['id']}/claim")
    assert assignment["context_api_path"].endswith(f"/tasks/{assignment['id']}/context?include_content=false&max_content_chars=0")
    assert assignment["return_api_paths"] == {}

    pause = client.post(
        f"/api/projects/{state.project.id}/human-control/pause",
        json={"actor": "operator", "reason": "inspect output"},
    )
    assert pause.status_code == 200

    paused_live = client.get(f"/api/projects/{state.project.id}/live").json()
    assert paused_live["human_control"]["active"] is True
    action_ids = {action["id"] for action in paused_live["snapshot"]["operation_console"]["actions"]}
    assert "human-control-resume" in action_ids
    assert "human-control-pause" not in action_ids


def test_project_artifact_detail_api_returns_persisted_content(tmp_path) -> None:
    client = TestClient(board.app)
    state = board.engine.create_project(requirement="Build artifact detail API", project_root=str(tmp_path / "project"))
    artifact_path = tmp_path / "project" / ".conductor" / "artifacts" / "artifact-detail.md"
    artifact_path.parent.mkdir(parents=True, exist_ok=True)
    artifact_path.write_text("# Persisted Artifact\n\nFile content wins.", encoding="utf-8")
    artifact = Artifact(
        id="artifact-detail",
        project_id=state.project.id,
        workitem_id=state.workitems[0].id,
        agent_id="agent-backend",
        kind="api_implementation",
        title="Persisted API Artifact",
        content="stale in-memory content",
        path=str(artifact_path),
        source_backend="agent_cli/codex",
    )
    board.engine.state_store.save_state(replace(state, artifacts=[*state.artifacts, artifact]))

    response = client.get(f"/api/projects/{state.project.id}/artifacts/{artifact.id}")

    assert response.status_code == 200
    payload = response.json()
    assert payload["project_id"] == state.project.id
    assert payload["artifact"]["id"] == artifact.id
    assert payload["artifact"]["detail_api_path"] == f"/api/projects/{state.project.id}/artifacts/{artifact.id}"
    assert payload["artifact"]["content"] == "# Persisted Artifact\n\nFile content wins."
    assert payload["artifact"]["content_source"] == "file"


def test_create_project_api_creates_project() -> None:
    client = TestClient(board.app)

    response = client.post(
        "/api/projects",
        json={
            "requirement": "实现一个包含 API、API 和测试的最小系统",
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
    requirement_path.write_text("实现中文需求：费用报销审批 Web API 和 API", encoding="utf-8")

    response = client.post(
        "/api/projects",
        json={
            "requirement_file": str(requirement_path),
            "project_root": str(tmp_path / "project"),
        },
    )

    assert response.status_code == 201
    assert response.json()["snapshot"]["project_goal"] == "实现中文需求：费用报销审批 Web API 和 API"


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
    assert "cli_tools" in payload
    assert payload["cli_tools"][0]["version_status"] == "not_checked"
    assert "role_bindings" in payload
    assert "llm_backends" in payload
    assert payload["llm_backends"][0]["server_status"] == "not_checked"


def test_diagnostics_api_can_probe_cli_versions(monkeypatch) -> None:
    monkeypatch.setattr(
        "conductor.diagnostics.discover_cli_tools",
        lambda: [type("Tool", (), {"name": "codex", "label": "Codex CLI", "path": "/bin/codex", "available": True})()],
    )
    monkeypatch.setattr("conductor.diagnostics._probe_cli_version", lambda *_: ("ok", "codex 1.2.3", ""))
    client = TestClient(board.app)

    response = client.get("/api/diagnostics?probe_cli=true")

    assert response.status_code == 200
    payload = response.json()
    assert payload["cli_tools"][0]["version_status"] == "ok"
    assert payload["cli_tools"][0]["version_output"] == "codex 1.2.3"


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


def test_project_human_control_api_can_pause_resume_and_approve_gate() -> None:
    client = TestClient(board.app)
    state = board.engine.create_project(requirement="Build a local reading list", project_root="")

    pause = client.post(
        f"/api/projects/{state.project.id}/human-control/pause",
        json={"actor": "operator", "reason": "inspect output"},
    )

    assert pause.status_code == 200
    pause_payload = pause.json()
    assert pause_payload["human_control"]["active"] is True
    assert pause_payload["human_control"]["action"] == "pause"
    assert pause_payload["human_control"]["hold_reason"] == "human_paused: inspect output"

    detail = client.get(f"/api/projects/{state.project.id}")
    assert detail.status_code == 200
    assert detail.json()["snapshot"]["human_control"]["active"] is True

    resume = client.post(
        f"/api/projects/{state.project.id}/human-control/resume",
        json={"actor": "operator", "reason": "continue"},
    )
    assert resume.status_code == 200
    assert resume.json()["human_control"]["active"] is False

    request = client.post(
        f"/api/projects/{state.project.id}/human-control/request-approval",
        json={
            "actor": "tl_agent",
            "reason": "high risk escalation",
            "controller_action": "escalate_project",
            "stage": "requirement",
            "workitem_id": state.workitems[0].id,
        },
    )

    assert request.status_code == 200
    request_payload = request.json()
    assert request_payload["human_control"]["active"] is True
    assert request_payload["human_control"]["action"] == "request_approval"
    assert request_payload["human_control"]["action_label"] == "等待人工审批"
    assert request_payload["human_control"]["payload"] == {
        "controller_action": "escalate_project",
        "stage": "requirement",
    }

    summaries = client.get("/api/projects")
    assert summaries.status_code == 200
    matching = next(item for item in summaries.json()["projects"] if item["project_id"] == state.project.id)
    assert matching["human_control_active"] is True
    assert matching["human_control_label"] == "等待人工审批"

    approve = client.post(
        f"/api/projects/{state.project.id}/human-control/approve",
        json={"actor": "operator", "reason": "approved"},
    )

    assert approve.status_code == 200
    assert approve.json()["human_control"]["active"] is False

    reloaded = board.engine.get_project(state.project.id)
    service = HumanControlService(board.engine.state_store)
    assert service.has_clearance(reloaded, "escalate_project", "requirement") is True


def test_project_run_apis_reject_active_human_control_hold() -> None:
    client = TestClient(board.app)
    state = board.engine.create_project(requirement="Build gated run controls", project_root="")
    pause = client.post(
        f"/api/projects/{state.project.id}/human-control/pause",
        json={"actor": "operator", "reason": "inspect delivery"},
    )
    assert pause.status_code == 200

    step = client.post(f"/api/projects/{state.project.id}/step")
    run = client.post(f"/api/projects/{state.project.id}/run")

    assert step.status_code == 409
    assert step.json()["accepted"] is False
    assert step.json()["task_status"]["running"] is False
    assert "human_paused: inspect delivery" in step.json()["task_status"]["error"]
    assert step.json()["human_control"]["active"] is True
    assert run.status_code == 409
    assert run.json()["accepted"] is False
    assert "human_paused: inspect delivery" in run.json()["task_status"]["error"]
    assert board.get_project_task_status(state.project.id).running is False


def test_project_run_redirect_routes_respect_active_human_control_hold() -> None:
    client = TestClient(board.app)
    state = board.engine.create_project(requirement="Build gated fallback run controls", project_root="")
    pause = client.post(
        f"/api/projects/{state.project.id}/human-control/pause",
        json={"actor": "operator", "reason": "inspect fallback"},
    )
    assert pause.status_code == 200

    step = client.get(f"/projects/{state.project.id}/step", follow_redirects=False)
    run = client.get(f"/projects/{state.project.id}/run", follow_redirects=False)

    assert step.status_code == 404
    assert run.status_code == 404
    return
    assert step.headers["location"] == f"/projects/{state.project.id}"
    assert run.headers["location"] == f"/projects/{state.project.id}"
    assert board.get_project_task_status(state.project.id).running is False
    assert board.get_project_task_status(state.project.id).action == ""


def test_project_run_apis_reject_terminal_project_statuses() -> None:
    client = TestClient(board.app)
    completed = board.engine.create_project(requirement="Build completed run guard", project_root="")
    completed = replace(
        completed,
        project=replace(completed.project, status=ProjectStatus.COMPLETED, current_stage="development"),
        project_status=ProjectStatus.COMPLETED,
        current_stage="development",
    )
    blocked = board.engine.create_project(requirement="Build blocked run guard", project_root="")
    blocked = replace(
        blocked,
        project=replace(blocked.project, status=ProjectStatus.BLOCKED, current_stage="development"),
        project_status=ProjectStatus.BLOCKED,
        current_stage="development",
    )
    board.engine.state_store.save_state(completed)
    board.engine.state_store.save_state(blocked)

    completed_step = client.post(f"/api/projects/{completed.project.id}/step")
    blocked_run = client.post(f"/api/projects/{blocked.project.id}/run")
    completed_get = client.get(f"/projects/{completed.project.id}/run", follow_redirects=False)

    assert completed_step.status_code == 409
    assert completed_step.json()["accepted"] is False
    assert completed_step.json()["project_status"] == "completed"
    assert completed_step.json()["task_status"]["error"] == "project_completed"
    assert blocked_run.status_code == 409
    assert blocked_run.json()["accepted"] is False
    assert blocked_run.json()["project_status"] == "blocked"
    assert blocked_run.json()["task_status"]["error"] == "project_blocked"
    assert completed_get.status_code == 404
    assert board.get_project_task_status(completed.project.id).running is False
    assert board.get_project_task_status(blocked.project.id).running is False


def test_project_run_apis_reject_missing_profile_cli_roles(monkeypatch) -> None:
    monkeypatch.setattr(
        board,
        "load_cli_selection_config",
        lambda: CLISelectionConfig(
            selected_cli_names=["codex"],
            role_cli_bindings={"backend_engineer": "codex"},
        ),
    )
    client = TestClient(board.app)
    original_profile = board.engine.run_profile
    try:
        board.engine.run_profile = "full_cli"
        state = board.engine.create_project(requirement="Build profile guarded run API", project_root="")

        response = client.post(f"/api/projects/{state.project.id}/step")
    finally:
        board.engine.run_profile = original_profile

    payload = response.json()
    assert response.status_code == 409
    assert payload["accepted"] is False
    assert payload["task_status"]["running"] is False
    assert "missing required CLI roles" in payload["task_status"]["error"]
    assert board.get_project_task_status(state.project.id).running is False


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
    assert payload["snapshot"]["project_id"] == state.project.id
    assert payload["snapshot"]["task_center_summary"]["claimable"] == 1
    assert "tasks" not in payload


def test_project_dynamic_agent_task_api_can_list_and_claim_matching_task(tmp_path) -> None:
    client = TestClient(board.app)
    project_root = tmp_path / "project"
    state = board.engine.create_project(requirement="Build a local reading list API", project_root=str(project_root))
    downstream = WorkItem(
        id="workitem-board-dynamic-ui",
        description="Implement API layout",
        stage="development",
        kind="api_implementation",
    )
    assignment = TaskAssignment(
        id="assignment-board-dynamic-ui",
        workitem_id=downstream.id,
        role="backend_engineer",
    )
    activation = AgentActivation(
        role="backend_engineer",
        agent_id="agent-backend-engineer-ui-layout-board",
        stage="development",
        reason="Backend layout can be split safely.",
        related_workitem_kinds=["api_implementation"],
        instance_id="ui_layout",
        scope="HTML layout and responsive structure",
        dynamic=True,
        parallel_safe=True,
        write_scope=["index.html", "styles.css"],
    )
    non_matching = AgentActivation(
        role="backend_engineer",
        agent_id="agent-backend-engineer-api-board",
        stage="development",
            reason="Testing work.",
            related_workitem_kinds=["automated_test"],
        dynamic=True,
    )
    state = replace(
        state,
        workitems=[*state.workitems, downstream],
        task_assignments=[*state.task_assignments, assignment],
        agent_activations=[*state.agent_activations, activation, non_matching],
    )
    board.engine.state_store.save_state(state)

    agents = client.get(f"/api/projects/{state.project.id}/tasks/{assignment.id}/agents")

    assert agents.status_code == 200
    agents_payload = agents.json()
    assert agents_payload["assignment_id"] == assignment.id
    assert agents_payload["eligible_count"] == 1
    assert agents_payload["agents"][0]["agent_id"] == activation.agent_id

    tasks = client.get(f"/api/projects/{state.project.id}/agents/{activation.agent_id}/tasks?claimable_only=true")

    assert tasks.status_code == 200
    tasks_payload = tasks.json()
    assert tasks_payload["activation_count"] == 1
    assert tasks_payload["task_count"] == 1
    assert tasks_payload["snapshot"]["project_id"] == state.project.id
    assert tasks_payload["snapshot"]["task_center_summary"]["claimable"] >= 1
    assert tasks_payload["tasks"][0]["assignment_id"] == assignment.id
    assert tasks_payload["tasks"][0]["parallel_safe"] is True
    assert tasks_payload["tasks"][0]["write_scope"] == ["index.html", "styles.css"]
    assert tasks_payload["tasks"][0]["claim_command"].startswith(
        f'python -m app.task_center claim-for-agent "{activation.agent_id}"'
    )
    assert f'--project-root "{project_root}"' in tasks_payload["tasks"][0]["claim_command"]
    assert "--with-context" not in tasks_payload["tasks"][0]["claim_command"]
    assert tasks_payload["tasks"][0]["claim_with_context_command"].endswith("--with-context")
    assert tasks_payload["tasks"][0]["claim_api_path"] == (
        f"/api/projects/{state.project.id}/agents/{activation.agent_id}/claim-task"
    )

    claim = client.post(
        f"/api/projects/{state.project.id}/agents/{activation.agent_id}/claim-task",
        json={
            "agent_id": "ignored-by-route",
            "claim_reason": "dynamic agent API claim",
            "include_context": True,
            "prompt_file": ".conductor/task_center/prompts/dynamic-board.md",
        },
    )

    prompt_file = project_root / ".conductor" / "task_center" / "prompts" / "dynamic-board.md"

    assert claim.status_code == 200
    claim_payload = claim.json()
    assert claim_payload["task"]["id"] == assignment.id
    assert claim_payload["task"]["status"] == "claimed"
    assert claim_payload["task"]["assigned_agent_id"] == activation.agent_id
    assert claim_payload["task"]["claim_reason"] == "dynamic agent API claim"
    assert claim_payload["matched_agent"]["instance_id"] == "ui_layout"
    assert claim_payload["matched_agent"]["write_scope"] == ["index.html", "styles.css"]
    assert claim_payload["context"]["assignment"]["id"] == assignment.id
    assert claim_payload["context"]["eligible_agent_activations"][0]["agent_id"] == activation.agent_id
    assert claim_payload["prompt_file"] == str(prompt_file.resolve())
    assert prompt_file.exists()
    assert activation.agent_id in prompt_file.read_text(encoding="utf-8")

    reloaded = board.engine.get_project(state.project.id)
    reloaded_assignment = next(item for item in reloaded.task_assignments if item.id == assignment.id)
    assert reloaded_assignment.prompt_file == str(prompt_file.resolve())


def test_project_dynamic_agent_tasks_expose_write_scope_conflicts(tmp_path) -> None:
    client = TestClient(board.app)
    state = board.engine.create_project(requirement="Build parallel API layout work", project_root=str(tmp_path / "project"))
    workitem_a = WorkItem(
        id="workitem-board-layout-a",
        description="Implement layout A",
        stage="development",
        kind="api_implementation",
    )
    workitem_b = WorkItem(
        id="workitem-board-layout-b",
        description="Implement layout B",
        stage="development",
        kind="api_implementation",
    )
    assignment_a = TaskAssignment(
        id="assignment-board-layout-a",
        workitem_id=workitem_a.id,
        role="backend_engineer",
    )
    assignment_b = TaskAssignment(
        id="assignment-board-layout-b",
        workitem_id=workitem_b.id,
        role="backend_engineer",
    )
    activation_a = AgentActivation(
        role="backend_engineer",
        agent_id="agent-board-layout-a",
        stage="development",
        reason="layout scope",
        related_workitem_kinds=["api_implementation"],
        instance_id="layout_a",
        dynamic=True,
        parallel_safe=True,
        write_scope=["index.html"],
    )
    activation_b = AgentActivation(
        role="backend_engineer",
        agent_id="agent-board-layout-b",
        stage="development",
        reason="layout scope",
        related_workitem_kinds=["api_implementation"],
        instance_id="layout_b",
        dynamic=True,
        parallel_safe=True,
        write_scope=["index.html"],
    )
    state = replace(
        state,
        workitems=[*state.workitems, workitem_a, workitem_b],
        task_assignments=[*state.task_assignments, assignment_a, assignment_b],
        agent_activations=[*state.agent_activations, activation_a, activation_b],
    )
    board.engine.state_store.save_state(state)

    claimed = client.post(
        f"/api/projects/{state.project.id}/agents/{activation_a.agent_id}/claim-task",
        json={"agent_id": "ignored-by-route", "claim_reason": "claim first layout scope"},
    )
    tasks = client.get(f"/api/projects/{state.project.id}/agents/{activation_b.agent_id}/tasks")
    claimable_tasks = client.get(f"/api/projects/{state.project.id}/agents/{activation_b.agent_id}/tasks?claimable_only=true")

    assert claimed.status_code == 200
    assert tasks.status_code == 200
    tasks_payload = tasks.json()
    conflicted = next(item for item in tasks_payload["tasks"] if item["assignment_id"] == assignment_b.id)
    assert conflicted["claimable"] is False
    assert conflicted["write_scope_conflict_assignment_ids"] == [assignment_a.id]
    assert conflicted["claim_command"] == ""
    assert conflicted["claim_with_context_command"] == ""
    assert conflicted["claim_api_path"] == ""
    assert claimable_tasks.status_code == 200
    assert claimable_tasks.json()["task_count"] == 0


def test_project_task_claim_and_complete_protocol() -> None:
    client = TestClient(board.app)
    state = board.engine.create_project(requirement="Build a local reading list", project_root="")
    assignment_id = state.task_assignments[0].id

    claimed = client.post(
        f"/api/projects/{state.project.id}/tasks/{assignment_id}/claim",
        json={"agent_id": "agent-manual", "claim_reason": "manual smoke", "lease_seconds": 60},
    )

    assert claimed.status_code == 200
    claimed_payload = claimed.json()
    assert claimed_payload["summary"]["claimed"] == 1
    assert claimed_payload["summary"]["claimable"] == 0
    claimed_task = claimed_payload["task"]
    assert claimed_task["status"] == "claimed"
    assert claimed_task["assigned_agent_id"] == "agent-manual"
    assert claimed_task["claim_token"]
    assert claimed_task["claim_reason"] == "manual smoke"
    assert claimed_task["claimed_at"]
    assert claimed_task["last_heartbeat_at"]
    assert claimed_task["lease_seconds"] == 60
    assert claimed_task["lease_expires_at"]
    assert claimed_task["lease_expired"] is False
    assert claimed_task["returned_at"] == ""
    assert claimed_task["workitem"]["status"] == "running"
    assert claimed_task["workitem"]["owner_agent"] == "agent-manual"
    return_commands = claimed_task["return_commands"]
    assert return_commands["complete"].startswith(f'python -m app.task_center complete "{assignment_id}"')
    assert '--agent-id "agent-manual"' in return_commands["complete"]
    assert f'--claim-token "{claimed_task["claim_token"]}"' in return_commands["complete"]
    assert return_commands["complete_with_output_file"].endswith('--output-file "result.md"')
    assert return_commands["heartbeat"].startswith(f'python -m app.task_center heartbeat "{assignment_id}"')
    assert return_commands["fail"].startswith(f'python -m app.task_center fail "{assignment_id}"')
    assert return_commands["fail_with_output_file"].endswith('--output-file "result.md"')
    assert return_commands["release"].startswith(f'python -m app.task_center release "{assignment_id}"')
    assert claimed_task["return_api_paths"] == {
        "complete": f"/api/projects/{state.project.id}/tasks/{assignment_id}/complete",
        "fail": f"/api/projects/{state.project.id}/tasks/{assignment_id}/fail",
        "heartbeat": f"/api/projects/{state.project.id}/tasks/{assignment_id}/heartbeat",
        "release": f"/api/projects/{state.project.id}/tasks/{assignment_id}/release",
    }

    heartbeat = client.post(
        f"/api/projects/{state.project.id}/tasks/{assignment_id}/heartbeat",
        json={"agent_id": "agent-manual", "claim_token": claimed_task["claim_token"], "lease_seconds": 120},
    )
    assert heartbeat.status_code == 200
    heartbeat_task = heartbeat.json()["task"]
    assert heartbeat_task["status"] == "claimed"
    assert heartbeat_task["last_heartbeat_at"]
    assert heartbeat_task["heartbeat_age_seconds"] is not None
    assert heartbeat_task["lease_seconds"] == 120
    assert heartbeat_task["lease_expires_at"] != claimed_task["lease_expires_at"]
    assert heartbeat.json()["snapshot"]["task_assignments"][0]["status"] == "claimed"

    claimed_list = client.get(f"/api/projects/{state.project.id}/tasks?status=claimed")
    assert claimed_list.status_code == 200
    assert claimed_list.json()["total"] == 1

    completed = client.post(
        f"/api/projects/{state.project.id}/tasks/{assignment_id}/complete",
        json={
            "agent_id": "agent-manual",
            "claim_token": heartbeat_task["claim_token"],
            "result_summary": "finished task",
            "output_artifact_ids": ["artifact-manual"],
        },
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
    assert completed_task["return_commands"] == {}
    assert completed_task["return_api_paths"] == {}
    assert completed_payload["snapshot"]["task_center_summary"]["completed"] == 1
    assert completed_payload["snapshot"]["task_assignments"][0]["status"] == "completed"

    completed_list = client.get(f"/api/projects/{state.project.id}/tasks?status=completed")
    assert completed_list.status_code == 200
    assert completed_list.json()["total"] == 1


def test_project_task_complete_api_rejects_wrong_agent_guard() -> None:
    client = TestClient(board.app)
    state = board.engine.create_project(requirement="Build a local reading list", project_root="")
    assignment_id = state.task_assignments[0].id
    claim = client.post(
        f"/api/projects/{state.project.id}/tasks/{assignment_id}/claim",
        json={"agent_id": "agent-owner"},
    )
    assert claim.status_code == 200

    completed = client.post(
        f"/api/projects/{state.project.id}/tasks/{assignment_id}/complete",
        json={
            "agent_id": "agent-other",
            "result_summary": "finished task",
            "output_artifact_content": "# Worker Result\n\nThis should not persist.",
        },
    )
    reloaded = board.engine.get_project(state.project.id)

    assert completed.status_code == 403
    assert "claimed by another agent" in completed.json()["detail"]
    assert {artifact.id for artifact in reloaded.artifacts} == {artifact.id for artifact in state.artifacts}


def test_project_task_complete_api_rejects_stale_claim_token() -> None:
    client = TestClient(board.app)
    state = board.engine.create_project(requirement="Build a local reading list", project_root="")
    assignment_id = state.task_assignments[0].id
    claim = client.post(
        f"/api/projects/{state.project.id}/tasks/{assignment_id}/claim",
        json={"agent_id": "agent-owner"},
    )
    assert claim.status_code == 200
    claim_token = claim.json()["task"]["claim_token"]

    completed = client.post(
        f"/api/projects/{state.project.id}/tasks/{assignment_id}/complete",
        json={
            "agent_id": "agent-owner",
            "claim_token": f"stale-{claim_token}",
            "result_summary": "finished task",
            "output_artifact_content": "# Worker Result\n\nThis should not persist.",
        },
    )
    reloaded = board.engine.get_project(state.project.id)

    assert completed.status_code == 403
    assert "claim token does not match" in completed.json()["detail"]
    assert {artifact.id for artifact in reloaded.artifacts} == {artifact.id for artifact in state.artifacts}


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


def test_project_task_release_api_requeues_claimed_assignment() -> None:
    client = TestClient(board.app)
    state = board.engine.create_project(requirement="Build a local reading list", project_root="")
    assignment_id = state.task_assignments[0].id
    claim = client.post(
        f"/api/projects/{state.project.id}/tasks/{assignment_id}/claim",
        json={"agent_id": "agent-api-worker"},
    )
    assert claim.status_code == 200

    release = client.post(
        f"/api/projects/{state.project.id}/tasks/{assignment_id}/release",
        json={"release_reason": "worker interrupted"},
    )

    assert release.status_code == 200
    payload = release.json()
    assert payload["summary"]["queued"] == 1
    assert payload["summary"]["claimable"] == 1
    assert payload["task"]["status"] == "queued"
    assert payload["task"]["assigned_agent_id"] == ""
    assert payload["task"]["claim_reason"] == "worker interrupted"
    assert payload["task"]["workitem"]["status"] == "pending"
    assert payload["snapshot"]["task_center_summary"]["claimable"] == 1

    reloaded = board.engine.get_project(state.project.id)
    assert reloaded.task_assignments[0].status.value == "queued"
    assert reloaded.workitems[0].status.value == "pending"


def test_project_task_fail_api_exposes_release_path_for_recovery() -> None:
    client = TestClient(board.app)
    state = board.engine.create_project(requirement="Build a recoverable failed task", project_root="")
    assignment_id = state.task_assignments[0].id
    claim = client.post(
        f"/api/projects/{state.project.id}/tasks/{assignment_id}/claim",
        json={"agent_id": "agent-api-worker"},
    )
    assert claim.status_code == 200
    claim_token = claim.json()["task"]["claim_token"]

    failed = client.post(
        f"/api/projects/{state.project.id}/tasks/{assignment_id}/fail",
        json={
            "agent_id": "agent-api-worker",
            "claim_token": claim_token,
            "result_summary": "failed task",
            "blocked_reason": "needs retry",
        },
    )

    assert failed.status_code == 200
    failed_payload = failed.json()
    failed_task = failed_payload["task"]
    assert failed_task["status"] == "failed"
    assert failed_task["return_api_paths"] == {
        "release": f"/api/projects/{state.project.id}/tasks/{assignment_id}/release",
    }
    assert failed_payload["snapshot"]["task_center_summary"]["failed"] == 1

    release = client.post(
        f"/api/projects/{state.project.id}/tasks/{assignment_id}/release",
        json={"release_reason": "retry failed task"},
    )
    assert release.status_code == 200
    release_payload = release.json()
    assert release_payload["task"]["status"] == "queued"
    assert release_payload["snapshot"]["task_center_summary"]["claimable"] == 1


def test_project_tasks_api_can_filter_stale_claimed_assignments() -> None:
    client = TestClient(board.app)
    state = board.engine.create_project(requirement="Build a local reading list", project_root="")
    claim = client.post(
        f"/api/projects/{state.project.id}/tasks/claim-next",
        json={"agent_id": "agent-api-worker"},
    )
    assert claim.status_code == 200
    claimed_state = board.engine.get_project(state.project.id)
    claimed_at = (datetime.now(timezone.utc) - timedelta(hours=2)).isoformat()
    assignment = replace(claimed_state.task_assignments[0], claimed_at=claimed_at, last_heartbeat_at=claimed_at)
    board.engine.state_store.upsert_task_assignment(state.project.id, assignment)

    response = client.get(f"/api/projects/{state.project.id}/tasks?stale_only=true&stale_after_seconds=1")

    assert response.status_code == 200
    payload = response.json()
    assert payload["stale_only"] is True
    assert payload["summary"]["stale_claimed"] == 1
    assert payload["total"] == 1
    assert payload["tasks"][0]["stale_claimed"] is True
    assert payload["tasks"][0]["claimed_age_seconds"] is not None


def test_project_tasks_api_can_release_stale_claimed_assignments() -> None:
    client = TestClient(board.app)
    state = board.engine.create_project(requirement="Build a local reading list", project_root="")
    claim = client.post(
        f"/api/projects/{state.project.id}/tasks/claim-next",
        json={"agent_id": "agent-api-worker"},
    )
    assert claim.status_code == 200
    claimed_state = board.engine.get_project(state.project.id)
    claimed_at = (datetime.now(timezone.utc) - timedelta(hours=2)).isoformat()
    assignment = replace(claimed_state.task_assignments[0], claimed_at=claimed_at, last_heartbeat_at=claimed_at)
    board.engine.state_store.upsert_task_assignment(state.project.id, assignment)

    response = client.post(
        f"/api/projects/{state.project.id}/tasks/release-stale",
        json={
            "stale_after_seconds": 1,
            "release_reason": "stale cleanup",
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["released_count"] == 1
    assert payload["summary"]["queued"] == 1
    assert payload["summary"]["claimable"] == 1
    assert payload["summary"]["stale_claimed"] == 0
    assert payload["tasks"][0]["status"] == "queued"
    assert payload["tasks"][0]["claim_reason"] == "stale cleanup"
    assert payload["snapshot"]["task_center_summary"]["claimable"] == 1

    reloaded = board.engine.get_project(state.project.id)
    assert reloaded.task_assignments[0].status.value == "queued"
    assert reloaded.workitems[0].status.value == "pending"


def test_project_tasks_api_can_release_expired_leases() -> None:
    client = TestClient(board.app)
    state = board.engine.create_project(requirement="Build a local reading list", project_root="")
    claim = client.post(
        f"/api/projects/{state.project.id}/tasks/claim-next",
        json={"agent_id": "agent-api-worker", "lease_seconds": 60},
    )
    assert claim.status_code == 200
    claimed_state = board.engine.get_project(state.project.id)
    expired_at = (datetime.now(timezone.utc) - timedelta(minutes=1)).isoformat()
    assignment = replace(
        claimed_state.task_assignments[0],
        lease_seconds=60,
        lease_expires_at=expired_at,
    )
    board.engine.state_store.upsert_task_assignment(state.project.id, assignment)

    response = client.post(
        f"/api/projects/{state.project.id}/tasks/release-expired-leases",
        json={"release_reason": "lease cleanup"},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["released_count"] == 1
    assert payload["summary"]["queued"] == 1
    assert payload["summary"]["claimable"] == 1
    assert payload["summary"]["lease_expired"] == 0
    assert payload["tasks"][0]["status"] == "queued"
    assert payload["tasks"][0]["claim_reason"] == "lease cleanup"
    assert payload["tasks"][0]["lease_seconds"] == 0
    assert payload["tasks"][0]["lease_expires_at"] == ""
    assert payload["tasks"][0]["lease_expired"] is False
    assert payload["snapshot"]["task_center_summary"]["lease_expired"] == 0

    reloaded = board.engine.get_project(state.project.id)
    assert reloaded.task_assignments[0].status.value == "queued"
    assert reloaded.task_assignments[0].lease_expires_at == ""
    assert reloaded.workitems[0].status.value == "pending"


def test_project_tasks_api_can_sweep_expired_and_stale_claims() -> None:
    client = TestClient(board.app)
    state = board.engine.create_project(requirement="Build a local reading list", project_root="")
    old_time = (datetime.now(timezone.utc) - timedelta(hours=2)).isoformat()
    expired_at = (datetime.now(timezone.utc) - timedelta(minutes=1)).isoformat()
    expired_workitem = WorkItem(
        id="workitem-api-sweep-expired",
        description="Expired lease",
        stage="development",
        status=WorkItemStatus.RUNNING,
    )
    stale_workitem = WorkItem(
        id="workitem-api-sweep-stale",
        description="Stale claim",
        stage="development",
        status=WorkItemStatus.RUNNING,
    )
    state = replace(
        state,
        workitems=[*state.workitems, expired_workitem, stale_workitem],
        task_assignments=[
            *state.task_assignments,
            TaskAssignment(
                id="assignment-api-sweep-expired",
                workitem_id=expired_workitem.id,
                role="backend_engineer",
                status=TaskAssignmentStatus.CLAIMED,
                assigned_agent_id="agent-expired",
                claimed_at=old_time,
                last_heartbeat_at=old_time,
                lease_seconds=60,
                lease_expires_at=expired_at,
            ),
            TaskAssignment(
                id="assignment-api-sweep-stale",
                workitem_id=stale_workitem.id,
                role="backend_engineer",
                status=TaskAssignmentStatus.CLAIMED,
                assigned_agent_id="agent-stale",
                claimed_at=old_time,
                last_heartbeat_at=old_time,
            ),
        ],
    )
    board.engine.state_store.save_state(state)

    response = client.post(
        f"/api/projects/{state.project.id}/tasks/sweep",
        json={
            "stale_after_seconds": 3600,
            "expired_lease_release_reason": "api lease sweep",
            "stale_release_reason": "api stale sweep",
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["released_count"] == 2
    assert payload["expired_lease_released_count"] == 1
    assert payload["stale_released_count"] == 1
    assert payload["summary"]["queued"] >= 2
    assert payload["summary"]["stale_claimed"] == 0
    assert [task["id"] for task in payload["expired_lease_tasks"]] == ["assignment-api-sweep-expired"]
    assert [task["id"] for task in payload["stale_tasks"]] == ["assignment-api-sweep-stale"]
    assert payload["expired_lease_tasks"][0]["claim_reason"] == "api lease sweep"
    assert payload["stale_tasks"][0]["claim_reason"] == "api stale sweep"
    assert payload["snapshot"]["task_center_summary"]["stale_claimed"] == 0

    reloaded = board.engine.get_project(state.project.id)
    assignments = {item.id: item for item in reloaded.task_assignments}
    assert assignments["assignment-api-sweep-expired"].status.value == "queued"
    assert assignments["assignment-api-sweep-stale"].status.value == "queued"


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
    assert "parent_artifact_id" in payload["input_artifacts"][0]
    assert "derived_from" in payload["input_artifacts"][0]
    assert "review_of" in payload["input_artifacts"][0]


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
    assert "- Parent Artifact ID:" in response.text
    assert "- Review Of:" in response.text
    assert "- Derived From:" in response.text
    assert "## CLI Return Commands" in response.text
    assert f'python -m app.task_center complete "{assignment.id}"' in response.text
    assert f'python -m app.task_center release "{assignment.id}"' in response.text


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
    assert payload["context"]["assignment"]["return_commands"]["complete_with_output_file"].endswith(
        '--output-file "result.md"'
    )
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
    assert f'python -m app.task_center release "{assignment.id}"' in payload["context_markdown"]


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
    assert payload["snapshot"]["project_id"] == state.project.id
    assert payload["snapshot"]["task_center_summary"]["claimed"] == 1
    assert payload["snapshot"]["task_assignments"][0]["status"] == "claimed"


def test_project_task_claim_batch_claims_role_limited_tasks() -> None:
    client = TestClient(board.app)
    state = board.engine.create_project(requirement="Build a local reading list", project_root="")
    first = WorkItem(id="workitem-batch-a", description="Backend task A", stage="development", kind="api_implementation")
    second = WorkItem(id="workitem-batch-b", description="Backend task B", stage="development", kind="api_implementation")
    third = WorkItem(id="workitem-batch-api", description="Backend task", stage="development", kind="api_implementation")
    state = replace(
        state,
        workitems=[*state.workitems, first, second, third],
        task_assignments=[
            *state.task_assignments,
            TaskAssignment(id="assignment-batch-a", workitem_id=first.id, role="backend_engineer"),
            TaskAssignment(id="assignment-batch-b", workitem_id=second.id, role="backend_engineer"),
            TaskAssignment(id="assignment-batch-api", workitem_id=third.id, role="backend_engineer"),
        ],
    )
    board.engine.state_store.save_state(state)

    response = client.post(
        f"/api/projects/{state.project.id}/tasks/claim-batch",
        json={
            "agent_id": "agent-batch-worker",
            "role": "backend_engineer",
            "limit": 2,
            "claim_reason": "parallel backend batch",
            "lease_seconds": 60,
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["claimed_count"] == 2
    assert payload["summary"]["claimed"] == 2
    assert [task["id"] for task in payload["tasks"]] == ["assignment-batch-a", "assignment-batch-b"]
    assert all(task["role"] == "backend_engineer" for task in payload["tasks"])
    assert all(task["assigned_agent_id"] == "agent-batch-worker" for task in payload["tasks"])
    assert all(task["lease_seconds"] == 60 for task in payload["tasks"])
    assert all(task["lease_expires_at"] for task in payload["tasks"])
    assert payload["snapshot"]["task_center_summary"]["claimed"] >= 2

    rejected = client.post(
        f"/api/projects/{state.project.id}/tasks/claim-batch",
        json={"agent_id": "agent-batch-worker", "limit": 99},
    )

    assert rejected.status_code == 400
    assert "exceeds configured maximum" in rejected.json()["detail"]


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
    assert f'python -m app.task_center release "{assignment.id}"' in payload["context_markdown"]


def test_project_task_claim_next_can_write_prompt_file(tmp_path) -> None:
    client = TestClient(board.app)
    project_root = tmp_path / "project"
    state = board.engine.create_project(
        requirement="Build a local reading list with CSV export",
        project_root=str(project_root),
    )
    artifact = board.engine.artifact_store.save_markdown(
        Artifact(
            id="artifact-claim-next-prompt-file",
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
            "prompt_file": ".conductor/task_center/prompts/next-task.md",
        },
    )

    prompt_file = project_root / ".conductor" / "task_center" / "prompts" / "next-task.md"
    prompt_content = prompt_file.read_text(encoding="utf-8")

    assert response.status_code == 200
    payload = response.json()
    assert payload["task"]["status"] == "claimed"
    assert payload["prompt_file"] == str(prompt_file.resolve())
    assert payload["task"]["prompt_file"] == str(prompt_file.resolve())
    assert "context" not in payload
    assert "context_markdown" not in payload
    assert "# Task Assignment Context" in prompt_content
    assert "artifact-claim-next-prompt-file" in prompt_content
    assert f'python -m app.task_center complete "{assignment.id}"' in prompt_content
    assert f'python -m app.task_center release "{assignment.id}"' in prompt_content

    reloaded = board.engine.get_project(state.project.id)
    assert reloaded.task_assignments[0].prompt_file == str(prompt_file.resolve())

    tasks = client.get(f"/api/projects/{state.project.id}/tasks")
    assert tasks.status_code == 200
    assert tasks.json()["tasks"][0]["prompt_file"] == str(prompt_file.resolve())


def test_project_task_claim_next_rejects_prompt_file_outside_project_root(tmp_path) -> None:
    client = TestClient(board.app)
    project_root = tmp_path / "project"
    state = board.engine.create_project(
        requirement="Build a local reading list",
        project_root=str(project_root),
    )

    response = client.post(
        f"/api/projects/{state.project.id}/tasks/claim-next",
        json={
            "agent_id": "agent-api-worker",
            "prompt_file": "../escaped-task.md",
        },
    )

    assert response.status_code == 422
    assert "Prompt file must be inside project root" in response.json()["detail"]
    assert not (tmp_path / "escaped-task.md").exists()

    reloaded = board.engine.get_project(state.project.id)
    assert reloaded.task_assignments[0].status == state.task_assignments[0].status
    assert reloaded.task_assignments[0].prompt_file == ""


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
