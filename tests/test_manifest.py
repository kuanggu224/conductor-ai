"""Run manifest tests."""

import json
from dataclasses import replace
from datetime import datetime, timedelta, timezone

from conductor.config.cli import CLISelectionConfig
from conductor.config.execution import RunProfile
from conductor.controller.engine import ConductorEngine
from conductor.domain.models import TaskAssignmentStatus


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

    assert payload["schema_version"] == "1.18"
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
    assert "requirement_coverage_results" in payload
    assert payload["requirement_evaluations"]
    assert payload["requirement_evaluations"][0]["kind"] == "requirement_spec"
    assert "score" in payload["requirement_evaluations"][0]
    assert "artifact_ids" in payload["executions"][0]
    assert isinstance(payload["executions"][0]["artifact_ids"], list)
    assert "input_artifact_ids" in payload["executions"][0]
    assert isinstance(payload["executions"][0]["input_artifact_ids"], list)
    assert "changed_files" in payload["executions"][0]
    assert "validation_command" in payload["executions"][0]
    assert "execution_command" in payload["executions"][0]
    assert "execution_exit_code" in payload["executions"][0]
    assert "execution_duration_ms" in payload["executions"][0]
    assert "failure_summary" in payload["executions"][0]
    assert "remediation_suggestions" in payload["executions"][0]
    assert payload["workitems"]
    assert "failure_type" in payload["workitems"][0]
    assert "remediation_suggestions" in payload["workitems"][0]
    assert "acceptance_criteria" in payload["workitems"][0]
    assert isinstance(payload["workitems"][0]["acceptance_criteria"], list)
    assert payload["task_assignments"]
    assert payload["task_assignments"][0]["workitem_id"] == payload["workitems"][0]["id"]
    assert "assigned_agent_id" in payload["task_assignments"][0]
    assert "claimed_at" in payload["task_assignments"][0]
    assert "claimed_age_seconds" in payload["task_assignments"][0]
    assert "stale_claimed" in payload["task_assignments"][0]
    assert "returned_at" in payload["task_assignments"][0]
    assert "prompt_file" in payload["task_assignments"][0]
    assert "claimable" in payload["task_assignments"][0]
    assert "unmet_dependency_ids" in payload["task_assignments"][0]
    assert isinstance(payload["task_assignments"][0]["unmet_dependency_ids"], list)
    assert payload["artifact_files"]
    assert payload["artifacts"]
    assert "workitem_id" in payload["artifacts"][0]
    assert "title" in payload["artifacts"][0]
    assert "parent_artifact_id" in payload["artifacts"][0]
    assert "derived_from" in payload["artifacts"][0]
    assert "review_of" in payload["artifacts"][0]
    assert "collaboration_session_id" in payload["artifacts"][0]
    assert "task_prompt_files" in payload
    assert isinstance(payload["task_prompt_files"], list)
    assert payload["files"]["log"].endswith(f"{state.project.id}.jsonl")
    assert payload["files"]["report"] == str(report_path)
    assert "task_prompts" in payload["files"]
    assert payload["summary"]["execution_count"] == len(state.executions)
    assert "requirement_quality_score" in payload["summary"]
    assert "requirement_coverage_status" in payload["summary"]
    assert payload["summary"]["workitem_status_counts"]
    assert payload["summary"]["execution_status_counts"]
    assert isinstance(payload["summary"]["failed_workitem_ids"], list)
    assert isinstance(payload["summary"]["blocked_reasons"], list)
    assert "retryable_failure_count" in payload["summary"]
    assert "non_retryable_failure_count" in payload["summary"]
    assert payload["summary"]["cli_run_count"] == len(payload["cli_runs"])
    assert payload["summary"]["llm_run_count"] == len(payload["llm_runs"])
    assert payload["summary"]["collaboration_run_count"] == len(payload["collaboration_runs"])
    assert isinstance(payload["summary"]["changed_files"], list)
    assert payload["summary"]["changed_file_count"] == len(payload["summary"]["changed_files"])
    assert payload["summary"]["artifact_file_count"] == len(payload["artifact_files"])
    assert payload["summary"]["task_prompt_file_count"] == len(payload["task_prompt_files"])
    assert "validation_failure_count" in payload["summary"]
    assert payload["summary"]["task_center_summary"]["total"] == len(state.task_assignments)
    assert "claimable" in payload["summary"]["task_center_summary"]
    assert "blocked_by_dependencies" in payload["summary"]["task_center_summary"]
    assert payload["run_environment"]["python_executable"]
    assert payload["run_environment"]["python_version"]
    assert payload["run_environment"]["process_cwd"]
    assert isinstance(payload["run_environment"]["command_argv"], list)
    assert "PATH" not in payload["run_environment"]["environment"]
    assert payload["platform_diagnostics"]["project_root"] == state.project.project_root
    assert payload["platform_diagnostics"]["llm_backends"][0]["server_status"] == "not_checked"
    assert "config_paths" in payload["platform_diagnostics"]
    assert payload["log_path"].endswith(f"{state.project.id}.jsonl")


def test_manifest_indexes_task_prompt_files(tmp_path) -> None:
    engine = ConductorEngine(
        log_dir=tmp_path / "logs",
        artifact_dir=tmp_path / "artifacts",
        cli_selection_config=CLISelectionConfig(),
        run_profile=RunProfile.MOCK,
    )
    state = engine.create_project("Build a local reading list", project_root=str(tmp_path / "project"))
    prompt_file = str(tmp_path / "project" / ".conductor" / "task_center" / "prompts" / "next-task.md")
    assignment = replace(state.task_assignments[0], prompt_file=prompt_file)
    state = engine.state_store.upsert_task_assignment(state.project.id, assignment)
    report_path = engine.write_project_report(state.project.id)

    manifest_path = engine.write_run_manifest(state.project.id, report_path)
    payload = json.loads(manifest_path.read_text(encoding="utf-8"))

    assert payload["task_assignments"][0]["prompt_file"] == prompt_file
    assert payload["task_prompt_files"] == [prompt_file]
    assert payload["files"]["task_prompts"] == [prompt_file]


def test_manifest_records_stale_claimed_task_assignments(tmp_path) -> None:
    engine = ConductorEngine(
        log_dir=tmp_path / "logs",
        artifact_dir=tmp_path / "artifacts",
        cli_selection_config=CLISelectionConfig(),
        run_profile=RunProfile.MOCK,
    )
    state = engine.create_project("Build a local reading list", project_root=str(tmp_path / "project"))
    claimed_at = (datetime.now(timezone.utc) - timedelta(hours=2)).isoformat()
    assignment = replace(
        state.task_assignments[0],
        status=TaskAssignmentStatus.CLAIMED,
        assigned_agent_id="agent-designer",
        claimed_at=claimed_at,
    )
    state = engine.state_store.upsert_task_assignment(state.project.id, assignment)
    report_path = engine.write_project_report(state.project.id)

    manifest_path = engine.write_run_manifest(state.project.id, report_path)
    payload = json.loads(manifest_path.read_text(encoding="utf-8"))

    assert payload["summary"]["task_center_summary"]["stale_claimed"] == 1
    assert payload["task_assignments"][0]["claimed_age_seconds"] >= 7200
    assert payload["task_assignments"][0]["stale_claimed"] is True


def test_manifest_records_artifact_lineage_metadata(tmp_path) -> None:
    from dataclasses import replace
    from conductor.domain.models import Artifact

    engine = ConductorEngine(
        log_dir=tmp_path / "logs",
        artifact_dir=tmp_path / "artifacts",
        cli_selection_config=CLISelectionConfig(),
        run_profile=RunProfile.MOCK,
    )
    state = engine.create_project("Build a local reading list", project_root=str(tmp_path / "project"))
    state = replace(
        state,
        artifacts=[
            Artifact(
                id="artifact-parent",
                project_id=state.project.id,
                workitem_id="workitem-design",
                agent_id="agent-designer",
                kind="design_overview",
                title="Parent design",
                content="parent",
            ),
            Artifact(
                id="artifact-review",
                project_id=state.project.id,
                workitem_id="workitem-review",
                agent_id="agent-reviewer",
                kind="collaboration_review",
                title="Review",
                content="review",
                parent_artifact_id="artifact-parent",
                derived_from=["artifact-parent"],
                review_of="artifact-parent",
                collaboration_session_id="collaboration-1",
            ),
        ],
    )
    engine.state_store.save_state(state)
    report_path = engine.write_project_report(state.project.id)

    manifest_path = engine.write_run_manifest(state.project.id, report_path)
    payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    review = next(artifact for artifact in payload["artifacts"] if artifact["id"] == "artifact-review")

    assert review["workitem_id"] == "workitem-review"
    assert review["title"] == "Review"
    assert review["parent_artifact_id"] == "artifact-parent"
    assert review["derived_from"] == ["artifact-parent"]
    assert review["review_of"] == "artifact-parent"
    assert review["collaboration_session_id"] == "collaboration-1"


def test_manifest_records_requirement_coverage_results(tmp_path) -> None:
    from conductor.domain.models import Artifact, Execution, ExecutionStatus, WorkItem

    engine = ConductorEngine(
        log_dir=tmp_path / "logs",
        artifact_dir=tmp_path / "artifacts",
        cli_selection_config=CLISelectionConfig(),
        run_profile=RunProfile.MOCK,
    )
    state = engine.create_project(
        "\u7528\u6237\u53ef\u4ee5\u6dfb\u52a0\u4e66\u7c4d\uff0c"
        "\u5237\u65b0\u540e\u4fdd\u7559\u6570\u636e\uff0c"
        "\u5e76\u5bfc\u51fa CSV\u3002"
    )
    validation_item = WorkItem(
        id="workitem-validation",
        description="\u9a8c\u6536\u9700\u6c42\u8986\u76d6",
        stage="testing",
        kind="acceptance_check",
    )
    state = replace(
        state,
        workitems=[validation_item],
        executions=[
            Execution(
                workitem_id=validation_item.id,
                agent_id="agent-tester",
                result="\n".join(
                    [
                        "Browser form interaction updated visible state: sample",
                        "Browser export/download action triggered",
                    ]
                ),
                status=ExecutionStatus.FAILED,
                source_backend="cli/static_web",
            )
        ],
        artifacts=[
            Artifact(
                id="artifact-frozen",
                project_id=state.project.id,
                workitem_id="workitem-requirement",
                agent_id="agent-requirement-designer",
                kind="frozen_requirement_spec",
                title="Frozen Requirement",
                content=state.project.goal,
            )
        ],
    )
    engine.state_store.save_state(state)
    report_path = engine.write_project_report(state.project.id)

    manifest_path = engine.write_run_manifest(state.project.id, report_path)
    payload = json.loads(manifest_path.read_text(encoding="utf-8"))

    assert payload["summary"]["requirement_coverage_status"] == "missing_coverage"
    assert payload["requirement_coverage_results"] == [
        {
            "workitem_id": "workitem-validation",
            "agent_id": "agent-tester",
            "status": "missing_coverage",
            "passed": False,
            "required_rules": ["add_item", "persistence", "export_csv"],
            "covered_rules": ["add_item", "export_csv"],
            "missing_rules": ["persistence"],
            "missing_labels": ["refresh persistence"],
            "traceability": [
                {
                    "rule_id": "add_item",
                    "label": "add item interaction",
                    "status": "covered",
                    "requirement_terms": ["\u6dfb\u52a0"],
                    "evidence_terms": ["browser form interaction updated visible state"],
                },
                {
                    "rule_id": "persistence",
                    "label": "refresh persistence",
                    "status": "missing",
                    "requirement_terms": ["\u5237\u65b0\u540e", "\u4fdd\u7559\u6570\u636e"],
                    "evidence_terms": [],
                },
                {
                    "rule_id": "export_csv",
                    "label": "CSV export/download",
                    "status": "covered",
                    "requirement_terms": ["csv", "\u5bfc\u51fa"],
                    "evidence_terms": ["browser export/download action triggered"],
                },
            ],
            "summary": "Requirement coverage missing: refresh persistence",
        }
    ]


def test_manifest_records_failure_remediation_suggestions(tmp_path) -> None:
    from conductor.domain.models import Execution, ExecutionStatus, WorkItem, WorkItemStatus

    engine = ConductorEngine(
        log_dir=tmp_path / "logs",
        artifact_dir=tmp_path / "artifacts",
        cli_selection_config=CLISelectionConfig(),
        run_profile=RunProfile.MOCK,
    )
    state = engine.create_project("Build a local reading list", project_root=str(tmp_path / "project"))
    failed_workitem = WorkItem(
        id="workitem-timeout",
        description="Run slow validation",
        stage="testing",
        kind="automated_test",
        status=WorkItemStatus.FAILED,
        failure_type="timeout",
        retryable=True,
        failure_summary="Agent CLI timed out",
    )
    state = replace(
        state,
        current_stage="testing",
        workitems=[failed_workitem],
        executions=[
            Execution(
                workitem_id=failed_workitem.id,
                agent_id="agent-tester",
                result="timeout",
                status=ExecutionStatus.FAILED,
                failure_type="timeout",
                failure_summary="Agent CLI timed out",
            )
        ],
    )
    engine.state_store.save_state(state)
    report_path = engine.write_project_report(state.project.id)

    manifest_path = engine.write_run_manifest(state.project.id, report_path)
    payload = json.loads(manifest_path.read_text(encoding="utf-8"))

    assert "Increase the CLI or LLM timeout" in payload["workitems"][0]["remediation_suggestions"][0]
    assert payload["executions"][0]["remediation_suggestions"]
    assert payload["summary"]["failed_workitem_ids"] == ["workitem-timeout"]
    assert payload["summary"]["retryable_failure_count"] == 1
    assert payload["summary"]["non_retryable_failure_count"] == 0


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
                execution_command=["codex", "exec", "-"],
                execution_exit_code=0,
                execution_duration_ms=123,
                changed_files=["app.py"],
                validation_command=["python", "-m", "pytest", "-q"],
                validation_exit_code=0,
                validation_success=True,
                cli_stdout_tail="done",
                input_artifact_ids=["artifact-design"],
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
    assert payload["executions"][0]["execution_command"] == ["codex", "exec", "-"]
    assert payload["executions"][0]["execution_exit_code"] == 0
    assert payload["executions"][0]["execution_duration_ms"] == 123
    assert payload["executions"][0]["input_artifact_ids"] == ["artifact-design"]
    assert payload["executions"][0]["changed_files"] == ["app.py"]
    assert payload["executions"][0]["validation_success"] is True
    assert payload["executions"][0]["cli_stdout_tail"] == "done"
    assert payload["summary"]["cli_run_count"] == 1
    assert payload["summary"]["changed_files"] == ["app.py"]
    assert payload["summary"]["changed_file_count"] == 1
    assert payload["summary"]["validation_failure_count"] == 0


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
