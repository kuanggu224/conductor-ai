"""Run manifest tests."""

import json

from conductor.config.cli import CLISelectionConfig
from conductor.config.execution import RunProfile
from conductor.controller.engine import ConductorEngine


def test_engine_writes_run_manifest(tmp_path) -> None:
    engine = ConductorEngine(
        log_dir=tmp_path / "logs",
        artifact_dir=tmp_path / "artifacts",
        cli_selection_config=CLISelectionConfig(),
        run_profile=RunProfile.MOCK,
    )
    state = engine.create_project("实现一个 API")
    state = engine.step_project(state.project.id)
    report_path = engine.write_project_report(state.project.id)

    manifest_path = engine.write_run_manifest(state.project.id, report_path)
    payload = json.loads(manifest_path.read_text(encoding="utf-8"))

    assert payload["schema_version"] == "1.2"
    assert payload["run_id"].startswith(state.project.id)
    assert payload["project_id"] == state.project.id
    assert payload["run_profile"] == "mock"
    assert payload["final_status"] == state.project_status.value
    assert payload["working_directory"] == state.project.project_root
    assert payload["report_path"] == str(report_path)
    assert payload["agents"]
    assert "cli_name" in payload["agents"][0]
    assert "model" in payload["agents"][0]
    assert payload["executions"]
    assert "llm_runs" in payload
    assert "collaboration_runs" in payload
    assert "team_plan" in payload["collaboration_runs"][0]
    assert "requirement_evaluations" in payload
    assert payload["requirement_evaluations"]
    assert payload["requirement_evaluations"][0]["kind"] == "requirement_spec"
    assert "score" in payload["requirement_evaluations"][0]
    assert "artifact_ids" in payload["executions"][0]
    assert isinstance(payload["executions"][0]["artifact_ids"], list)
    assert "changed_files" in payload["executions"][0]
    assert "validation_command" in payload["executions"][0]
    assert "failure_summary" in payload["executions"][0]
    assert payload["workitems"]
    assert "failure_type" in payload["workitems"][0]
    assert payload["artifact_files"]
    assert payload["files"]["log"].endswith(f"{state.project.id}.jsonl")
    assert payload["files"]["report"] == str(report_path)
    assert payload["summary"]["execution_count"] == len(state.executions)
    assert "requirement_quality_score" in payload["summary"]
    assert payload["log_path"].endswith(f"{state.project.id}.jsonl")


def test_manifest_records_codex_model_for_bound_agent(tmp_path) -> None:
    engine = ConductorEngine(
        log_dir=tmp_path / "logs",
        artifact_dir=tmp_path / "artifacts",
        cli_selection_config=CLISelectionConfig(
            selected_cli_names=["codex"],
            role_cli_bindings={"requirement_designer": "codex", "designer": "codex"},
            codex_model="gpt-5.4-mini",
            codex_reasoning_effort="medium",
        ),
    )
    state = engine.create_project("实现一个设计文档")
    report_path = engine.write_project_report(state.project.id)

    manifest_path = engine.write_run_manifest(state.project.id, report_path)
    payload = json.loads(manifest_path.read_text(encoding="utf-8"))

    designer = next(agent for agent in payload["agents"] if agent["role"] == "requirement_designer")
    assert designer["cli_name"] == "codex"
    assert designer["model"] == "gpt-5.4-mini/medium"


def test_manifest_extracts_cli_runs_from_agent_cli_artifacts(tmp_path) -> None:
    engine = ConductorEngine(
        log_dir=tmp_path / "logs",
        artifact_dir=tmp_path / "artifacts",
        cli_selection_config=CLISelectionConfig(
            selected_cli_names=["codex"],
            role_cli_bindings={"designer": "codex"},
            codex_model="gpt-5.4-mini",
            codex_reasoning_effort="medium",
        ),
    )
    state = engine.create_project("实现一个设计文档")
    artifact = state.artifacts
    report_path = engine.write_project_report(state.project.id)
    state = engine.get_project(state.project.id)
    # Avoid invoking real CLI in the test: synthesize one completed execution/artifact pair.
    from dataclasses import replace
    from conductor.domain.models import Artifact, Execution, ExecutionStatus

    workitem = state.workitems[0]
    state = replace(
        state,
        executions=[
            Execution(
                workitem_id=workitem.id,
                agent_id="agent-designer",
                result="ok",
                status=ExecutionStatus.SUCCESS,
                source_backend="agent_cli/codex",
                cli_name="codex",
                model="gpt-5.4-mini/medium",
                working_directory=str(tmp_path),
                changed_files=["app.py"],
                validation_command=["python", "-m", "pytest", "-q"],
                validation_exit_code=0,
                validation_success=True,
                cli_stdout_tail="done",
            )
        ],
        artifacts=[
            *artifact,
            Artifact(
                id="artifact-cli",
                project_id=state.project.id,
                workitem_id=workitem.id,
                agent_id="agent-designer",
                kind=workitem.kind,
                title="CLI artifact",
                content="ok",
                path=str(tmp_path / "artifact-cli.md"),
                source_backend="agent_cli/codex",
            ),
            Artifact(
                id="artifact-review",
                project_id=state.project.id,
                workitem_id=workitem.id,
                agent_id="agent-designer",
                kind="collaboration_review",
                title="Review artifact",
                content="review",
                path=str(tmp_path / "artifact-review.md"),
                source_backend="collaboration",
            ),
        ],
    )
    engine.state_store.save_state(state)

    manifest_path = engine.write_run_manifest(state.project.id, report_path)
    payload = json.loads(manifest_path.read_text(encoding="utf-8"))

    assert len(payload["cli_runs"]) == 1
    assert payload["cli_runs"][0]["cli_name"] == "codex"
    assert payload["cli_runs"][0]["model"] == "gpt-5.4-mini/medium"
    assert payload["executions"][0]["source_backend"] == "agent_cli/codex"
    assert payload["executions"][0]["changed_files"] == ["app.py"]
    assert payload["executions"][0]["validation_success"] is True
    assert payload["executions"][0]["cli_stdout_tail"] == "done"


def test_manifest_records_llm_harness_collaboration_runtime(tmp_path) -> None:
    from dataclasses import replace

    from conductor.collaboration.models import (
        Collaboration,
        CollaborationDraftVersion,
        CollaborationStatus,
        ReviewContribution,
        ReviewDecision,
    )
    from conductor.domain.models import AgentActivation, Artifact, Execution, ExecutionStatus

    engine = ConductorEngine(
        log_dir=tmp_path / "logs",
        artifact_dir=tmp_path / "artifacts",
        cli_selection_config=CLISelectionConfig(),
    )
    state = engine.create_project("design collaboration manifest")
    report_path = engine.write_project_report(state.project.id)
    state = engine.get_project(state.project.id)
    workitem = state.workitems[0]
    state = replace(
        state,
        executions=[
            Execution(
                workitem_id=workitem.id,
                agent_id="agent-designer",
                result="draft",
                status=ExecutionStatus.SUCCESS,
                source_backend="llm_harness/qwen/qwen3.6-35b-a3b",
                model="qwen/qwen3.6-35b-a3b",
                working_directory=str(tmp_path),
            )
        ],
        artifacts=[
            Artifact(
                id="artifact-draft",
                project_id=state.project.id,
                workitem_id=workitem.id,
                agent_id="agent-designer",
                kind=workitem.kind,
                title="draft",
                content="draft",
                path=str(tmp_path / "draft.md"),
                source_backend="llm_harness/qwen/qwen3.6-35b-a3b",
            )
        ],
        agent_activations=[
            AgentActivation(
                role="designer",
                agent_id="agent-designer",
                stage="design",
                reason="initial draft",
                execution_backend="mock",
            ),
            AgentActivation(
                role="requirement_designer",
                agent_id="agent-requirement-designer",
                stage="design",
                reason="peer review",
                execution_backend="mock",
            ),
        ],
        collaborations=[
            Collaboration(
                id="collaboration-workitem-001",
                project_id=state.project.id,
                workitem_id=workitem.id,
                lead_agent_id="agent-designer",
                reviewer_agent_ids=["agent-requirement-designer"],
                status=CollaborationStatus.MAX_ROUNDS_REACHED,
                max_rounds=2,
                current_round=1,
                contributions=[
                    ReviewContribution(
                        id="review-1",
                        round_index=1,
                        agent_id="agent-requirement-designer",
                        role="requirement_designer",
                        decision=ReviewDecision.REQUEST_CHANGES,
                        content="Decision: request_changes",
                        phase="design_peer_review",
                        source_backend="llm_harness/qwen/qwen3.6-35b-a3b",
                        model="qwen/qwen3.6-35b-a3b",
                        output_path=str(tmp_path / "review.md"),
                        duration_ms=1234,
                    )
                ],
                draft_versions=[
                    CollaborationDraftVersion(
                        version=1,
                        round_index=0,
                        author_agent_id="agent-designer",
                        content="draft",
                        source_backend="llm_harness/qwen/qwen3.6-35b-a3b",
                        model="qwen/qwen3.6-35b-a3b",
                        output_path=str(tmp_path / "draft.md"),
                        duration_ms=1000,
                    ),
                    CollaborationDraftVersion(
                        version=2,
                        round_index=1,
                        author_agent_id="agent-designer",
                        content="revision",
                        review_ids=["review-1"],
                        source_backend="llm_harness/qwen/qwen3.6-35b-a3b",
                        model="qwen/qwen3.6-35b-a3b",
                        output_path=str(tmp_path / "revision.md"),
                        duration_ms=2000,
                    ),
                ],
                final_artifact_id="artifact-collaboration-workitem-001",
            )
        ],
    )
    engine.state_store.save_state(state)

    manifest_path = engine.write_run_manifest(state.project.id, report_path)
    payload = json.loads(manifest_path.read_text(encoding="utf-8"))

    designer = next(agent for agent in payload["agents"] if agent["role"] == "designer")
    reviewer = next(agent for agent in payload["agents"] if agent["role"] == "requirement_designer")
    assert designer["execution_backend"] == "llm_harness"
    assert reviewer["execution_backend"] == "llm_harness"
    assert reviewer["review_count"] == 1
    assert reviewer["model"] == "qwen/qwen3.6-35b-a3b"
    assert len(payload["llm_runs"]) == 4
    assert any(run["mode"] == "workitem_execution" for run in payload["llm_runs"])
    assert any(run["mode"] == "collaboration_review" for run in payload["llm_runs"])
    assert any(run["mode"] == "collaboration_revision" for run in payload["llm_runs"])
    assert payload["collaboration_runs"][0]["phases"] == ["design_peer_review"]
    assert payload["collaboration_runs"][0]["reviews"][0]["duration_ms"] == 1234
