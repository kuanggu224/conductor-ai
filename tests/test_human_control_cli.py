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
