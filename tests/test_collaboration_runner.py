"""多 Agent 协作执行器测试。"""

from conductor.agents.registry import AgentRegistry
from conductor.artifacts.store import ArtifactStore
from conductor.collaboration.models import CollaborationStatus, ReviewDecision
from conductor.collaboration.policy import CollaborationPolicy
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


class FakeApprovalLLMHarness:
    name = "llm"

    def run(self, request):
        return LLMHarnessResult(
            success=True,
            content="Decision: approve\n\n### 主要问题\n- 无\n\n### 建议\n- 可以进入下一步\n",
            duration_ms=10,
            model_name=request.config.model_name,
            output_path=request.output_path,
        )


class FakeRequirementRevisionLLMHarness:
    name = "llm"

    def __init__(self) -> None:
        self.requests = []

    def run(self, request):
        self.requests.append(request)
        if request.metadata.get("mode") == "collaboration_review":
            content = (
                "Decision: request_changes\n\n"
                "## 主要问题\n"
                "- 需要明确刷新后的持久化策略、CSV 导出字段和可执行验收用例。\n\n"
                "## 建议\n"
                "- 补充 localStorage、字段校验、空数据导出和下游测试约束。\n\n"
                "## 风险\n"
                "- 数据丢失和导出格式不一致。\n\n"
                "## 可执行验收关注点\n"
                "- 添加书籍后刷新页面，数据仍存在；空清单导出 CSV 时给出反馈。"
            )
        else:
            content = (
                "# 个人读书清单 Web 应用需求规格\n\n"
                "## 目标\n"
                "为个人用户提供读书清单 Web 应用，支持添加书名、作者、阅读状态、评分、备注，"
                "按状态筛选，导出 CSV，并在刷新后保留数据。\n\n"
                "## 需求理解\n"
                "- 用户可以新增书籍记录并查看读书清单。\n"
                "- 阅读状态包括未读、在读、已读，可用于列表筛选。\n"
                "- CSV 导出包含书名、作者、阅读状态、评分、备注和更新时间字段。\n"
                "- 使用 localStorage 持久化单用户数据，刷新页面后恢复完整清单。\n\n"
                "## 范围边界\n"
                "- 仅交付单用户 Web 应用和浏览器本地持久化。\n"
                "- 不包含账号系统、云同步、推荐系统和社交分享。\n\n"
                "## 非目标\n"
                "- 不做多端同步。\n"
                "- 不做书籍封面上传。\n"
                "- 不做第三方登录。\n\n"
                "## 验收标准\n"
                "- 输入书名《三体》、作者刘慈欣、状态已读、评分 5、备注科幻后提交，列表出现该记录。\n"
                "- 将状态筛选为已读时，只显示已读书籍。\n"
                "- 导出 CSV 时，文件包含书名、作者、阅读状态、评分、备注和更新时间列。\n"
                "- 添加至少 2 条记录后刷新页面，所有记录仍存在。\n\n"
                "## 边界/异常场景\n"
                "- 书名或作者为空时阻止提交并显示错误。\n"
                "- 评分必须在 1 到 5 之间。\n"
                "- 空清单导出 CSV 时显示无数据反馈。\n"
                "- localStorage 写入失败时显示保存失败提示。\n\n"
                "## 风险与假设\n"
                "- 假设只服务单浏览器单用户场景。\n"
                "- 风险是用户清理浏览器数据会导致记录丢失。\n\n"
                "## 待确认问题\n"
                "- CSV 文件名是否需要包含日期？\n"
                "- 阅读状态枚举是否允许用户自定义？\n"
                "- 是否需要编辑或删除已有书籍？当前不纳入正式范围。\n\n"
                "## 下游交付约束\n"
                "- 前端实现必须覆盖空状态、错误状态和筛选状态。\n"
                "- 测试必须覆盖刷新持久化、CSV 导出字段和字段校验。"
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


def test_collaboration_runner_uses_kind_specific_lead_role(tmp_path) -> None:
    state_store = InMemoryStateStore()
    registry = AgentRegistry()
    artifact_store = ArtifactStore(tmp_path)
    runner = CollaborationRunner(
        state_store=state_store,
        registry=registry,
        artifact_store=artifact_store,
        policy=CollaborationPolicy(
            max_rounds=1,
            lead_role_by_stage={"development": "backend_engineer"},
            lead_role_by_kind={"ui_implementation": "frontend_engineer"},
            peer_reviewer_roles_by_stage={"development": ["backend_engineer", "frontend_engineer"]},
            reviewer_roles_by_stage={"development": ["solution_designer", "tester"]},
            enabled_kinds={"ui_implementation"},
        ),
        use_llm=False,
    )
    workitem = WorkItem(
        id="workitem-ui",
        description="Implement UI",
        stage="development",
        kind="ui_implementation",
    )
    state_store.save_state(
        SharedProjectState(
            project=Project(id="project-ui", goal="Build a UI", project_root=str(tmp_path)),
            project_status=ProjectStatus.IN_PROGRESS,
            current_stage="development",
            workitems=[workitem],
        )
    )
    artifact = Artifact(
        id="artifact-ui",
        project_id="project-ui",
        workitem_id=workitem.id,
        agent_id="agent-frontend",
        kind="ui_implementation",
        title="UI implementation",
        content="目标\n方案\n验收\n",
    )

    collaboration = runner.run_review_loop("project-ui", workitem, artifact)

    assert collaboration.lead_agent_id == "agent-frontend"
    assert runner.lead_role_for_workitem(workitem) == "frontend_engineer"
    assert "agent-frontend" not in collaboration.reviewer_agent_ids
    assert set(collaboration.reviewer_agent_ids) == {"agent-backend", "agent-solution-designer", "agent-tester"}
    assert collaboration.team_plan["lead_role"] == "frontend_engineer"
    assert collaboration.team_plan["functional_seats"]


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
    assert all("原始用户需求" in request.prompt for request in llm_harness.requests)
    assert any("CRUD" in request.prompt for request in llm_harness.requests if request.metadata.get("mode") == "collaboration_revision")
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


def test_requirement_quality_gate_can_fail_approved_but_weak_draft(tmp_path) -> None:
    state_store = InMemoryStateStore()
    registry = AgentRegistry()
    artifact_store = ArtifactStore(tmp_path)
    runner = CollaborationRunner(
        state_store=state_store,
        registry=registry,
        artifact_store=artifact_store,
        policy=CollaborationPolicy(
            enabled=True,
            max_rounds=1,
            lead_role_by_stage={"requirement": "requirement_designer"},
            peer_reviewer_roles_by_stage={"requirement": ["designer"]},
            reviewer_roles_by_stage={"requirement": []},
            enabled_kinds={"requirement_spec"},
        ),
        require_real_outputs=True,
        use_llm=False,
        llm_harness=FakeApprovalLLMHarness(),
        llm_harness_config=LLMHTTPConfig(
            base_url="http://127.0.0.1:1234/v1",
            model_name="reviewer-model",
            enabled=True,
        ),
    )
    workitem = WorkItem(
        id="workitem-req",
        description="澄清读书清单需求",
        stage="requirement",
        kind="requirement_spec",
    )
    draft = artifact_store.save_markdown(
        Artifact(
            id="artifact-workitem-req",
            project_id="project-quality-gate",
            workitem_id=workitem.id,
            agent_id="agent-requirement-designer",
            kind="requirement_spec",
            title="需求草案",
            content="可以做。",
            source_backend="llm_harness/reviewer-model",
        )
    )
    state_store.save_state(
        SharedProjectState(
            project=Project(
                id="project-quality-gate",
                goal="个人读书清单 Web 应用：添加书名、作者、阅读状态、评分、备注；按状态筛选；导出 CSV；刷新后保留数据。",
                current_stage="requirement",
                project_root=str(tmp_path),
            ),
            project_status=ProjectStatus.IN_PROGRESS,
            current_stage="requirement",
            workitems=[workitem],
            artifacts=[draft],
        )
    )

    collaboration = runner.run_review_loop("project-quality-gate", workitem, draft)
    latest = state_store.get_state("project-quality-gate")

    assert collaboration.status == CollaborationStatus.FAILED
    assert not any(artifact.kind == "frozen_requirement_spec" for artifact in latest.artifacts)
    assert any("需求质量门禁未通过" in event for event in latest.recent_events)


def test_requirement_final_arbitration_accepts_resolved_last_revision(tmp_path) -> None:
    state_store = InMemoryStateStore()
    registry = AgentRegistry()
    artifact_store = ArtifactStore(tmp_path)
    llm_harness = FakeRequirementRevisionLLMHarness()
    runner = CollaborationRunner(
        state_store=state_store,
        registry=registry,
        artifact_store=artifact_store,
        policy=CollaborationPolicy(
            enabled=True,
            max_rounds=1,
            lead_role_by_stage={"requirement": "requirement_designer"},
            peer_reviewer_roles_by_stage={"requirement": ["designer"]},
            reviewer_roles_by_stage={"requirement": []},
            enabled_kinds={"requirement_spec"},
            dynamic_requirement_review_enabled=False,
        ),
        require_real_outputs=True,
        use_llm=False,
        llm_harness=llm_harness,
        llm_harness_config=LLMHTTPConfig(
            base_url="http://127.0.0.1:1234/v1",
            model_name="reviewer-model",
            enabled=True,
        ),
    )
    workitem = WorkItem(
        id="workitem-final-arbitration",
        description="澄清读书清单需求",
        stage="requirement",
        kind="requirement_spec",
    )
    draft = artifact_store.save_markdown(
        Artifact(
            id="artifact-final-arbitration",
            project_id="project-final-arbitration",
            workitem_id=workitem.id,
            agent_id="agent-requirement-designer",
            kind="requirement_spec",
            title="需求草案",
            content="# 初稿\n\n支持读书清单。",
            source_backend="llm_harness/reviewer-model",
        )
    )
    state_store.save_state(
        SharedProjectState(
            project=Project(
                id="project-final-arbitration",
                goal="个人读书清单 Web 应用：添加书名、作者、阅读状态、评分、备注；按状态筛选；导出 CSV；刷新后保留数据。",
                current_stage="requirement",
                project_root=str(tmp_path),
            ),
            project_status=ProjectStatus.IN_PROGRESS,
            current_stage="requirement",
            workitems=[workitem],
            artifacts=[draft],
        )
    )

    collaboration = runner.run_review_loop("project-final-arbitration", workitem, draft)
    latest = state_store.get_state("project-final-arbitration")

    assert collaboration.status == CollaborationStatus.ACCEPTED
    assert [request.metadata.get("mode") for request in llm_harness.requests] == [
        "collaboration_review",
        "collaboration_revision",
    ]
    assert any(artifact.kind == "frozen_requirement_spec" for artifact in latest.artifacts)
    assert any("Requirement final arbitration" in event for event in latest.recent_events)


def test_requirement_mock_revision_can_pass_offline_smoke_gate(tmp_path) -> None:
    state_store = InMemoryStateStore()
    registry = AgentRegistry()
    artifact_store = ArtifactStore(tmp_path)
    runner = CollaborationRunner(
        state_store=state_store,
        registry=registry,
        artifact_store=artifact_store,
        policy=CollaborationPolicy(
            enabled=True,
            max_rounds=2,
            lead_role_by_stage={"requirement": "requirement_designer"},
            peer_reviewer_roles_by_stage={"requirement": ["designer"]},
            reviewer_roles_by_stage={"requirement": ["frontend_engineer", "tester"]},
            enabled_kinds={"requirement_spec"},
            dynamic_requirement_review_enabled=False,
        ),
        use_llm=False,
    )
    workitem = WorkItem(
        id="workitem-offline-smoke",
        description=(
            "Build a small local static web app for managing a reading list. "
            "Users can add a book with title, author, and priority, mark it finished, "
            "filter all active finished, and persist data in localStorage. "
            "No backend, no login, no cloud sync."
        ),
        stage="requirement",
        kind="requirement_spec",
        acceptance_criteria=[
            "The app has an index.html entry point.",
            "JavaScript updates visible UI state after adding and completing items.",
            "CSS is present and readable.",
            "Static validation passes without network access.",
        ],
    )
    draft = artifact_store.save_markdown(
        Artifact(
            id="artifact-offline-smoke",
            project_id="project-offline-smoke",
            workitem_id=workitem.id,
            agent_id="agent-requirement-designer",
            kind="requirement_spec",
            title="Initial requirement draft",
            content="# Initial draft\n\nReading list app.",
            source_backend="mock",
        ),
        project_root=tmp_path,
    )
    state_store.save_state(
        SharedProjectState(
            project=Project(
                id="project-offline-smoke",
                goal=workitem.description,
                current_stage="requirement",
                project_root=str(tmp_path),
            ),
            project_status=ProjectStatus.IN_PROGRESS,
            current_stage="requirement",
            workitems=[workitem],
            artifacts=[draft],
        )
    )

    collaboration = runner.run_review_loop("project-offline-smoke", workitem, draft)
    latest = state_store.get_state("project-offline-smoke")

    assert collaboration.status == CollaborationStatus.ACCEPTED
    assert any(artifact.kind == "frozen_requirement_spec" for artifact in latest.artifacts)


def test_requirement_collaboration_uses_dynamic_reviewer_seats(tmp_path) -> None:
    state_store = InMemoryStateStore()
    registry = AgentRegistry()
    artifact_store = ArtifactStore(tmp_path)
    runner = CollaborationRunner(
        state_store=state_store,
        registry=registry,
        artifact_store=artifact_store,
        policy=CollaborationPolicy(max_rounds=1),
    )
    workitem = WorkItem(
        id="workitem-dynamic-req",
        description="Clarify a complex requirement",
        stage="requirement",
        kind="requirement_spec",
    )
    draft = artifact_store.save_markdown(
        Artifact(
            id="artifact-dynamic-req",
            project_id="project-dynamic-team",
            workitem_id=workitem.id,
            agent_id="agent-requirement-designer",
            kind="requirement_spec",
            title="Requirement Draft",
            content="Initial draft",
            source_backend="mock",
        )
    )
    state_store.save_state(
        SharedProjectState(
            project=Project(
                id="project-dynamic-team",
                goal=(
                    "Build an expense approval web UI with employee and manager roles, REST API, "
                    "status workflow, validation errors, persisted data, CSV export, and audit permissions."
                ),
                current_stage="requirement",
                project_root=str(tmp_path),
            ),
            project_status=ProjectStatus.IN_PROGRESS,
            current_stage="requirement",
            workitems=[workitem],
            artifacts=[draft],
        )
    )

    collaboration = runner.run_review_loop("project-dynamic-team", workitem, draft)
    latest = state_store.get_state("project-dynamic-team")

    assert "agent-designer:designer.interaction" in collaboration.reviewer_agent_ids
    assert "agent-designer:designer.information_architecture" in collaboration.reviewer_agent_ids
    assert "agent-solution-designer:solution_designer.process" in collaboration.reviewer_agent_ids
    assert "agent-tester:tester.edge_cases" in collaboration.reviewer_agent_ids
    assert len([item for item in collaboration.contributions if item.role == "designer"]) >= 3
    assert collaboration.team_plan["complexity_level"] == "complex"
    assert any(
        seat["seat_id"] == "designer.interaction"
        for seat in collaboration.team_plan["peer_seats"]
    )
    assert any("Requirement review team planned" in event for event in latest.recent_events)
