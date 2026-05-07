"""多 Agent 协作执行器测试。"""

from conductor.agents.registry import AgentRegistry
from conductor.artifacts.store import ArtifactStore
from conductor.collaboration.models import CollaborationStatus, ReviewDecision
from conductor.collaboration.runner import CollaborationRunner
from conductor.config.cli import CLISelectionConfig
from conductor.agents.llm import LLMHTTPConfig
from conductor.domain.models import Artifact, Project, ProjectStatus, SharedProjectState, WorkItem
from conductor.harness.base import BaseHarness
from conductor.harness.llm import LLMHarnessResult
from conductor.harness.models import HarnessRequest, HarnessResult
from conductor.state.store import InMemoryStateStore


class FakeAgentCLIHarness(BaseHarness):
    name = "shell"

    def __init__(self) -> None:
        self.requests: list[HarnessRequest] = []

    def run(self, request: HarnessRequest) -> HarnessResult:
        self.requests.append(request)
        prompt = " ".join(request.command)
        if "不要修改原文" in prompt:
            return HarnessResult(
                success=True,
                exit_code=0,
                stdout="## 审阅意见\n\nDecision: request_changes\n\n### 主要问题\n- 需要补充边界说明\n",
                stderr="",
                duration_ms=10,
            )
        return HarnessResult(
            success=True,
            exit_code=0,
            stdout="# 修订版需求文档\n\n已根据审阅意见统一整理。",
            stderr="",
            duration_ms=10,
        )


class FakeCollaborationLLMHarness:
    name = "llm"

    def __init__(self) -> None:
        self.requests = []

    def run(self, request):
        self.requests.append(request)
        mode = request.metadata.get("mode")
        role = request.metadata.get("agent_role")
        if mode == "collaboration_review":
            content = (
                f"## {role} 审阅意见\n\n"
                "Decision: request_changes\n\n"
                "### 主要问题\n- 需要补充可执行验收用例。\n\n"
                "### 建议\n- 给出具体输入和期望输出。\n\n"
                "### 风险\n- 验收标准不可自动化。\n\n"
                "### 可执行验收关注点\n- 输入 12 元 Food 后总额显示 12。\n"
            )
        else:
            content = (
                "# 修订版需求设计文档\n\n"
                "## 目标\n完成真实修订。\n\n"
                "## 可执行验收用例\n- 输入 12 元 Food，期望总额显示 12。\n"
            )
        return LLMHarnessResult(
            success=True,
            content=content,
            duration_ms=10,
            model_name=request.config.model_name,
            output_path=request.output_path,
        )


def test_collaboration_runner_collects_all_reviews_before_revision(tmp_path) -> None:
    state_store = InMemoryStateStore()
    registry = AgentRegistry()
    artifact_store = ArtifactStore(tmp_path)
    runner = CollaborationRunner(
        state_store=state_store,
        registry=registry,
        artifact_store=artifact_store,
    )
    workitem = WorkItem(
        id="workitem-001",
        description="形成总体设计",
        stage="design",
        kind="design_overview",
    )
    draft = artifact_store.save_markdown(
        Artifact(
            id="artifact-workitem-001",
            project_id="project-1",
            workitem_id=workitem.id,
            agent_id="agent-designer",
            kind="design_overview",
            title="产品/设计文档 - workitem-001",
            content="初稿内容",
            source_backend="mock",
        )
    )
    state_store.save_state(
        SharedProjectState(
            project=Project(id="project-1", goal="实现任务管理", current_stage="design"),
            project_status=ProjectStatus.IN_PROGRESS,
            current_stage="design",
            workitems=[workitem],
            artifacts=[draft],
        )
    )

    collaboration = runner.run_review_loop("project-1", workitem, draft)
    latest = state_store.get_state("project-1")

    round_one_reviews = [
        contribution
        for contribution in collaboration.contributions
        if contribution.round_index == 1
    ]
    assert len(round_one_reviews) == 5
    assert {item.role for item in round_one_reviews} == {
        "requirement_designer",
        "solution_designer",
        "backend_engineer",
        "frontend_engineer",
        "tester",
    }
    assert [item.phase for item in round_one_reviews[:2]] == ["design_peer_review", "design_peer_review"]
    assert all(item.decision == ReviewDecision.REQUEST_CHANGES for item in round_one_reviews)
    assert collaboration.status in {CollaborationStatus.ACCEPTED, CollaborationStatus.MAX_ROUNDS_REACHED}
    assert collaboration.final_artifact_id == "artifact-collaboration-workitem-001"
    assert len(collaboration.draft_versions) >= 2
    assert collaboration.draft_versions[0].version == 1
    assert collaboration.draft_versions[0].round_index == 0
    collaboration_artifact = next(artifact for artifact in latest.artifacts if artifact.kind == "collaboration_review")
    assert collaboration_artifact.review_of == "artifact-workitem-001"
    assert collaboration_artifact.collaboration_session_id == collaboration.id
    assert "artifact-workitem-001" in collaboration_artifact.derived_from
    assert "Draft Versions" in collaboration_artifact.content
    assert latest.collaborations[0].id == collaboration.id


def test_collaboration_runner_uses_agent_cli_for_reviews_and_revision(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr("conductor.agents.cli_executor.shutil.which", lambda name: f"C:/bin/{name}.cmd")
    state_store = InMemoryStateStore()
    registry = AgentRegistry()
    artifact_store = ArtifactStore(tmp_path)
    harness = FakeAgentCLIHarness()
    runner = CollaborationRunner(
        state_store=state_store,
        registry=registry,
        artifact_store=artifact_store,
        cli_selection_config=CLISelectionConfig(
            selected_cli_names=["claude"],
            role_cli_bindings={
                "designer": "claude",
                "requirement_designer": "claude",
                "solution_designer": "claude",
                "backend_engineer": "claude",
                "frontend_engineer": "claude",
                "tester": "claude",
            },
        ),
    )
    runner.agent_cli_executor.shell_harness = harness
    workitem = WorkItem(
        id="workitem-002",
        description="形成总体设计",
        stage="design",
        kind="design_overview",
    )
    draft = artifact_store.save_markdown(
        Artifact(
            id="artifact-workitem-002",
            project_id="project-cli-review",
            workitem_id=workitem.id,
            agent_id="agent-designer",
            kind="design_overview",
            title="产品/设计文档 - workitem-002",
            content="初稿内容",
            source_backend="agent_cli/claude",
        )
    )
    state_store.save_state(
        SharedProjectState(
            project=Project(id="project-cli-review", goal="实现任务管理", current_stage="design"),
            project_status=ProjectStatus.IN_PROGRESS,
            current_stage="design",
            workitems=[workitem],
            artifacts=[draft],
        )
    )

    collaboration = runner.run_review_loop("project-cli-review", workitem, draft)
    latest = state_store.get_state("project-cli-review")

    assert collaboration.status == CollaborationStatus.MAX_ROUNDS_REACHED
    assert len(harness.requests) == 14
    assert all(request.command[0].lower().endswith("claude.cmd") for request in harness.requests)
    assert all(request.command[1] == "-p" for request in harness.requests)
    collaboration_artifact = next(artifact for artifact in latest.artifacts if artifact.kind == "collaboration_review")
    assert "已根据审阅意见统一整理" in collaboration_artifact.content


def test_collaboration_runner_uses_llm_harness_for_reviews_and_revision(tmp_path) -> None:
    state_store = InMemoryStateStore()
    registry = AgentRegistry()
    artifact_store = ArtifactStore(tmp_path)
    llm_harness = FakeCollaborationLLMHarness()
    runner = CollaborationRunner(
        state_store=state_store,
        registry=registry,
        artifact_store=artifact_store,
        require_real_outputs=True,
        use_llm=False,
        llm_harness=llm_harness,
        llm_harness_config=LLMHTTPConfig(
            base_url="http://127.0.0.1:1234/v1",
            model_name="qwen2.5-coder-14b-instruct",
            enabled=True,
        ),
    )
    workitem = WorkItem(
        id="workitem-003",
        description="形成总体设计",
        stage="design",
        kind="design_overview",
    )
    draft = artifact_store.save_markdown(
        Artifact(
            id="artifact-workitem-003",
            project_id="project-llm-review",
            workitem_id=workitem.id,
            agent_id="agent-designer",
            kind="design_overview",
            title="产品/设计文档 - workitem-003",
            content="# 初稿\n\n## 目标\n完成功能。",
            source_backend="llm_harness/qwen2.5-coder-14b-instruct",
        )
    )
    state_store.save_state(
        SharedProjectState(
            project=Project(
                id="project-llm-review",
                goal="实现开销记录器",
                current_stage="design",
                project_root=str(tmp_path),
            ),
            project_status=ProjectStatus.IN_PROGRESS,
            current_stage="design",
            workitems=[workitem],
            artifacts=[draft],
        )
    )

    collaboration = runner.run_review_loop("project-llm-review", workitem, draft)
    latest = state_store.get_state("project-llm-review")

    modes = [request.metadata.get("mode") for request in llm_harness.requests]
    assert modes.count("collaboration_review") == 10
    assert modes.count("collaboration_revision") == 4
    reviewer_roles = {
        request.metadata.get("agent_role")
        for request in llm_harness.requests
        if request.metadata.get("mode") == "collaboration_review"
    }
    assert reviewer_roles == {
        "requirement_designer",
        "solution_designer",
        "backend_engineer",
        "frontend_engineer",
        "tester",
    }
    phases = {request.metadata.get("phase") for request in llm_harness.requests}
    assert phases == {"design_peer_review", "cross_functional_review"}
    assert collaboration.status == CollaborationStatus.MAX_ROUNDS_REACHED
    collaboration_artifact = next(artifact for artifact in latest.artifacts if artifact.kind == "collaboration_review")
    assert "可执行验收用例" in collaboration_artifact.content
