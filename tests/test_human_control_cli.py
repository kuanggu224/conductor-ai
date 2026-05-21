"""Human control CLI tests."""

import json

from app.human_control import main
from conductor.control.human import HumanControlService
from conductor.domain.models import Project, ProjectStatus, SharedProjectState
from conductor.state.file_store import FileStateStore


def _seed_project(project_root, project_id: str = "project-human") -> SharedProjectState:
    state = SharedProjectState(
        project=Project(
            id=project_id,
            goal="Build a local tool",
            status=ProjectStatus.IN_PROGRESS,
            current_stage="development",
            project_root=str(project_root),
        ),
        project_status=ProjectStatus.IN_PROGRESS,
        current_stage="development",
    )
    FileStateStore(project_root / ".conductor" / "state").save_state(state)
    return state


def test_human_control_cli_pause_resume_and_status(tmp_path, capsys) -> None:
    project_root = tmp_path / "project"
    state = _seed_project(project_root)

    status_code = main(["status", "--project-root", str(project_root)])
    status_payload = json.loads(capsys.readouterr().out)

    assert status_code == 0
    assert status_payload["project_id"] == state.project.id
    assert status_payload["active"] is False
    assert status_payload["available_actions"] == ["pause", "request_approval"]
    assert "not held" in status_payload["operator_guidance"]
    assert status_payload["operator_commands"][0].startswith("python -m app.human_control pause")
    assert f"--project-id {state.project.id}" in status_payload["operator_commands"][0]

    pause_code = main(
        [
            "pause",
            "--project-root",
            str(project_root),
            "--actor",
            "operator",
            "--reason",
            "inspect delivery",
        ]
    )
    pause_payload = json.loads(capsys.readouterr().out)

    assert pause_code == 0
    assert pause_payload["active"] is True
    assert pause_payload["active_action"]["action"] == "pause"
    assert pause_payload["active_action"]["actor"] == "operator"
    assert pause_payload["hold_reason"] == "human_paused: inspect delivery"
    assert pause_payload["available_actions"] == ["resume", "override"]
    assert "python -m app.human_control resume" in pause_payload["operator_commands"][0]

    resume_code = main(
        [
            "resume",
            "--project-root",
            str(project_root),
            "--actor",
            "operator",
            "--reason",
            "continue",
        ]
    )
    resume_payload = json.loads(capsys.readouterr().out)

    assert resume_code == 0
    assert resume_payload["active"] is False
    assert resume_payload["hold_reason"] == ""
    assert [item["action"] for item in resume_payload["actions"]] == ["pause", "resume"]


def test_human_control_cli_approval_inherits_active_gate_payload(tmp_path, capsys) -> None:
    project_root = tmp_path / "project"
    state = _seed_project(project_root)

    request_code = main(
        [
            "request-approval",
            "--project-root",
            str(project_root),
            "--actor",
            "tl_agent",
            "--reason",
            "escalation needs approval",
            "--controller-action",
            "escalate_project",
            "--stage",
            "development",
        ]
    )
    request_payload = json.loads(capsys.readouterr().out)

    assert request_code == 0
    assert request_payload["active"] is True
    assert request_payload["available_actions"] == ["approve", "reject", "override"]
    assert "Approval is pending" in request_payload["operator_guidance"]
    assert "python -m app.human_control approve" in request_payload["operator_commands"][0]
    assert '--controller-action "escalate_project"' in request_payload["operator_commands"][0]
    assert '--stage "development"' in request_payload["operator_commands"][0]
    assert request_payload["active_action"]["payload"] == {
        "controller_action": "escalate_project",
        "stage": "development",
    }
    assert request_payload["hold_reason"] == "human_approval_required: escalation needs approval"

    approve_code = main(
        [
            "approve",
            "--project-root",
            str(project_root),
            "--actor",
            "operator",
            "--reason",
            "approved after review",
        ]
    )
    approve_payload = json.loads(capsys.readouterr().out)

    assert approve_code == 0
    assert approve_payload["active"] is False
    assert approve_payload["actions"][-1]["action"] == "approve"
    assert approve_payload["actions"][-1]["payload"] == {
        "controller_action": "escalate_project",
        "stage": "development",
    }

    reloaded_store = FileStateStore(project_root / ".conductor" / "state")
    reloaded = reloaded_store.get_state(state.project.id)
    service = HumanControlService(reloaded_store)
    assert service.has_clearance(reloaded, "escalate_project", "development") is True


def test_human_control_cli_requires_project_id_when_multiple_states(tmp_path, capsys) -> None:
    project_root = tmp_path / "workspace"
    _seed_project(project_root, project_id="project-a")
    _seed_project(project_root, project_id="project-b")

    code = main(["status", "--project-root", str(project_root)])
    captured = capsys.readouterr()

    assert code == 2
    assert "Multiple project states found" in captured.err

    code = main(["status", "--project-root", str(project_root), "--project-id", "project-b"])
    payload = json.loads(capsys.readouterr().out)

    assert code == 0
    assert payload["project_id"] == "project-b"


def test_human_control_cli_status_all_reports_active_holds(tmp_path, capsys) -> None:
    project_root = tmp_path / "workspace"
    state_a = _seed_project(project_root, project_id="project-a")
    state_b = _seed_project(project_root, project_id="project-b")
    state_c = _seed_project(project_root, project_id="project-c")
    store = FileStateStore(project_root / ".conductor" / "state")
    service = HumanControlService(store)
    service.pause(state_b.project.id, actor="operator", reason="inspect output")
    service.request_approval(
        state_c.project.id,
        actor="tl_agent",
        reason="high risk escalation",
        payload={"controller_action": "escalate_project", "stage": "development"},
    )

    code = main(["status-all", "--project-root", str(project_root)])
    payload = json.loads(capsys.readouterr().out)

    assert code == 0
    assert payload["project_count"] == 3
    assert payload["active_only"] is False
    assert payload["active_count"] == 2
    assert payload["active_project_ids"] == ["project-b", "project-c"]
    assert payload["action_count"] == 2
    assert [item["action"] for item in payload["active_actions"]] == ["pause", "request_approval"]

    projects = {item["project_id"]: item for item in payload["projects"]}
    assert projects[state_a.project.id]["active"] is False
    assert projects[state_a.project.id]["available_actions"] == ["pause", "request_approval"]
    assert projects[state_b.project.id]["hold_reason"] == "human_paused: inspect output"
    assert projects[state_b.project.id]["operator_commands"][0].startswith("python -m app.human_control resume")
    assert projects[state_c.project.id]["hold_reason"] == "human_approval_required: high risk escalation"
    assert projects[state_c.project.id]["active_action"]["payload"] == {
        "controller_action": "escalate_project",
        "stage": "development",
    }
    assert "python -m app.human_control approve" in projects[state_c.project.id]["operator_commands"][0]


def test_human_control_cli_status_all_allows_empty_workspace(tmp_path, capsys) -> None:
    project_root = tmp_path / "workspace"

    code = main(["status-all", "--project-root", str(project_root)])
    payload = json.loads(capsys.readouterr().out)

    assert code == 0
    assert payload == {
        "ok": True,
        "active_only": False,
        "project_count": 0,
        "active_count": 0,
        "active_project_ids": [],
        "active_actions": [],
        "action_count": 0,
        "projects": [],
    }


def test_human_control_cli_status_all_can_filter_active_and_fail_for_scheduler(tmp_path, capsys) -> None:
    project_root = tmp_path / "workspace"
    _seed_project(project_root, project_id="project-clean")
    held_state = _seed_project(project_root, project_id="project-held")
    store = FileStateStore(project_root / ".conductor" / "state")
    HumanControlService(store).pause(held_state.project.id, actor="operator", reason="review delivery")

    code = main(
        [
            "status-all",
            "--project-root",
            str(project_root),
            "--active-only",
            "--fail-on-active",
            "--output",
            "human-control/status.json",
        ]
    )
    payload = json.loads(capsys.readouterr().out)
    output_path = project_root / "human-control" / "status.json"
    output_payload = json.loads(output_path.read_text(encoding="utf-8"))

    assert code == 3
    assert payload["active_only"] is True
    assert payload["project_count"] == 1
    assert payload["active_count"] == 1
    assert payload["active_project_ids"] == ["project-held"]
    assert [item["project_id"] for item in payload["projects"]] == ["project-held"]
    assert payload["projects"][0]["hold_reason"] == "human_paused: review delivery"
    assert payload["output_path"] == str(output_path)
    assert "output_path" not in output_payload
    assert output_payload["active_project_ids"] == ["project-held"]


def test_human_control_cli_status_all_fail_on_active_passes_when_clean(tmp_path, capsys) -> None:
    project_root = tmp_path / "workspace"
    _seed_project(project_root, project_id="project-clean")

    code = main(["status-all", "--project-root", str(project_root), "--fail-on-active"])
    payload = json.loads(capsys.readouterr().out)

    assert code == 0
    assert payload["active_count"] == 0
    assert payload["project_count"] == 1
