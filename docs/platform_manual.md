# Conductor 平台说明文档

更新时间：2026-05-20

## 1. 平台定位

Conductor 是一个 AI Native 的 Project Execution System。它不是普通聊天工具，也不是简单的多 Agent 调度器，而是把多个 AI Agent 组织成一个可控的软件项目团队，由平台负责流程推进、状态记录、质量门禁和交付审计。

一句话定义：

```text
Conductor = Controller 控流程 + Agent 承担职责 + Shared State 记录事实 + Task Center 分发任务 + Manifest 审计交付
```

当前平台的重点不是让顶级模型单次生成更强答案，而是通过结构化流程、多 Agent 评审、上下文传递和质量门禁，放大小模型、本地模型、CLI Agent 在真实项目执行中的稳定性和交付质量。

## 2. 核心目标

Conductor 期望实现的完整链路是：

```text
自然语言需求
-> 需求理解与冻结
-> 多 Agent 设计评审
-> 任务拆解与分派
-> CLI / LLM / Harness 执行
-> 测试与验收
-> 返工与恢复
-> Manifest / Report / Artifact 归档
```

平台当前已经具备从需求阶段到设计、开发、测试、任务中心、日志审计的雏形，其中需求阶段和任务中心已经相对成熟；开发、测试、多 Agent 动态扩缩容、人类接管和生产级长周期执行仍在持续打磨。

## 3. 设计原则

### 3.1 Project First

平台以 `Project` 为核心，而不是以一次对话或一个 prompt 为核心。项目包含目标、阶段、任务、Agent、Artifact、日志、运行清单和最终状态。

### 3.2 Controller Owns Flow

Agent 不直接决定项目是否进入下一阶段。流程推进由 `LeadController` / `Engine` 和 `SharedProjectState` 控制。Agent 只在约束内完成指定任务，并把结果归还给平台。

### 3.3 Shared State Is Source of Truth

所有关键状态必须落入 `SharedProjectState` 或文件版状态存储中，包括：

- 当前阶段
- WorkItem
- Agent 激活记录
- Artifact
- Collaboration session
- TaskAssignment
- blocker
- retry / rework 历史
- event log

### 3.4 Agent Is Role, Backend Is Tool

Agent 表示职责、任务边界、输出契约和协作规则。模型或 CLI 只是执行后端。

一个 `backend_engineer` Agent 可以绑定：

- OpenAI-compatible API
- LM Studio 本地模型
- 九天云端模型
- Codex CLI
- Claude Code CLI
- OpenCode / AspireCode
- Qwen CLI
- ShellHarness
- Mock fallback

### 3.5 Auditability First

每次运行都应该能被追踪、复盘和验收。平台通过 Artifact、Event Log、Run Manifest、Task Center transition history、测试报告和质量评分来保存证据。

## 4. 默认流程

当前默认流程为：

```text
requirement -> design -> development -> testing
```

### 4.1 Requirement 阶段

目标：把用户的自然语言需求转成冻结的需求规格。

当前能力：

- 创建正式 `requirement_spec` WorkItem。
- 支持多个需求/设计相关 Agent 参与评审和修订。
- 支持静态评审团队和动态评审席位规划。
- 需求通过质量门禁后生成 `frozen_requirement_spec`。
- 后续设计、开发、测试必须基于冻结需求。
- 弱需求不会静默进入设计阶段。
- 需求质量不足时自动创建需求返工 WorkItem。
- 返工有上限，连续失败会阻塞项目并暴露 blocker。

典型动态评审席位：

- `designer.interaction`
- `designer.information_architecture`
- `solution_designer.process`
- `solution_designer.security_boundary`
- `backend_engineer.contracts`
- `frontend_engineer.states`
- `tester.edge_cases`

### 4.2 Design 阶段

目标：基于冻结需求形成设计方案、实现边界和下游交付约束。

当前能力：

- 支持设计初稿、review、revision 的协作流程。
- 支持跨职责评审，例如后端、前端、测试参与设计评审。
- 设计通过质量门禁后生成 `frozen_design_spec`。
- ContextBuilder、Task Center、Manifest 和后续阶段可读取冻结设计。
- 设计质量不足时创建设计返工 WorkItem。

### 4.3 Development 阶段

目标：由开发 Agent 真实执行代码级任务。

当前能力：

- 支持通过 CLI Agent 或 LLMHarness 执行任务。
- 支持 backend/frontend/tester 等角色分工。
- 支持失败分类、重试、返工 WorkItem。
- 支持外部 Agent 从 Task Center 领取任务并归还 Artifact。
- 支持静态 Web 小项目端到端验证。
- Development WorkItem 会把冻结需求和设计输入提升为显式 acceptance criteria，要求实现保持基线范围并说明必要设计偏离。

当前限制：

- 多 Agent 并行开发协议还不是生产级。
- 跨文件冲突、分支合并、代码所有权、长期任务拆分仍需增强。

### 4.4 Testing 阶段

目标：验证交付物是否满足冻结需求和冻结设计。

当前能力：

- `ShellHarness` 执行命令级测试。
- `StaticWebHarness` 验证静态 Web 项目。
- Requirement coverage 检查。
- API validation 需要输出 endpoint、status code 或 response payload 等具体接口行为证据；泛化的测试通过信息不能单独满足 API coverage。
- Harness 测试报告会渲染 `Testing Checklist Evidence Contract`，把 required evidence terms 写入持久化 Artifact。
- 测试失败可反馈生成开发返工任务。
- Manifest 记录测试结果、失败原因和修复建议。

## 5. 主要模块

```text
app/
  run_project.py              项目运行入口
  task_center.py              Task Center CLI
  requirement_benchmark.py    需求阶段 benchmark
  diagnostics.py              环境诊断
  verify_manifest.py          Manifest 校验
  replay_manifest.py          Manifest 回放
  board.py                    Web Board 后端入口

conductor/
  controller/                 LeadController、Engine、TL Agent 雏形
  workflow/                   默认阶段模板
  domain/                     Project、WorkItem、Artifact、Assignment 等模型
  collaboration/              多 Agent 协作、评审、修订
  execution/                  Runner、Planner、失败策略、运行流
  agents/                     Agent profile、CLI executor、LLM backend
  harness/                    ShellHarness、LLMHarness、StaticWebHarness
  context/                    ContextBuilder
  artifacts/                  Artifact 存储、契约和 lineage
  state/                      内存/文件状态存储
  task_center/                任务领取、上下文、归还协议
  board/                      Board snapshot 服务
  config/                     LLM、CLI、系统配置
  manifest.py                 Run Manifest
  replay_verifier.py          Manifest verifier
```

## 6. 多 Agent 协作模型

Conductor 的多 Agent 不是“多个 AI 轮流聊天”，而是结构化职责协作。

当前协作模式：

1. Lead Agent 产出初稿。
2. Reviewer Agents 按职责提出结构化评审意见。
3. 同一轮先收集所有 reviewer 意见。
4. Lead Agent 统一吸收意见并修订。
5. Controller 根据质量门禁和评审意见覆盖情况裁决是否接受。
6. 通过后冻结 Artifact，失败则生成返工任务。

已经实现：

- 多角色评审。
- 同职位多个评审席位。
- Team Plan 记录。
- Collaboration Runs 写入 Manifest。
- Board Snapshot 暴露协作信息。
- 需求阶段动态评审席位规划。
- Task Center 可按动态 Agent 激活记录查询和领取匹配任务。

仍需完善：

- TL Agent 的全局判断与项目风险控制。
- 更智能的实时 Agent 创建和团队扩缩容。
- 并行开发时的冲突控制和归并协议。
- 人类随时接管、审批、暂停、重试、覆盖决策。

## 7. 执行后端

### 7.1 LLMHarness

`LLMHarness` 用于调用 OpenAI-compatible API，生成受控文本或代码 Artifact。

已验证或已接入方向：

- LM Studio 本地模型。
- 九天云端模型。
- 其他兼容 OpenAI API 的服务。

本地配置文件：

```text
.conductor/llm.config.json
```

示例：

```json
{
  "cloud": {
    "cloud_llm_base_url": "https://jiutian.10086.cn/largemodel/moma/api/v3",
    "cloud_llm_model": "jiutian-lan-comv3",
    "cloud_llm_api_key": "<fill locally>",
    "cloud_llm_timeout": 120.0,
    "cloud_llm_enabled": true
  },
  "usage": {
    "runner_enabled": true,
    "preferred_backend": "cloud"
  }
}
```

注意：API key 只保存在本地配置文件，不应提交到 Git。

### 7.2 Agent CLI

平台支持扫描和绑定多种 Agent CLI：

- `codex`
- `claude`
- `qwen`
- `opencode`
- `aspirecode`
- `aider`
- `gemini`

CLI Agent 适合执行真实代码修改。平台负责准备上下文、工作目录、任务边界和验收要求；CLI Agent 负责在项目目录内落地文件。

### 7.3 ShellHarness

`ShellHarness` 用于执行受控 shell 命令，例如测试、构建、检查脚本。它也承担 Windows / Linux UTF-8 环境变量注入，减少中文输出和文件名乱码。

### 7.4 StaticWebHarness

`StaticWebHarness` 用于验证小型静态 Web 项目，包括：

- `index.html` 是否存在。
- JS/CSS 本地资源是否存在。
- JS 语法检查。
- 本地 HTTP 服务。
- 页面 smoke check。
- 基础交互验证。
- 导出、搜索、筛选、删除等常见 Web 行为检查。

## 8. Task Center

Task Center 是外部 Agent 或人类 worker 的轻量任务中心。它不是完整分布式队列，但已经定义了清晰的领取、续约、归还、失败和维护协议。

核心能力：

- 查看可领取任务。
- claim / claim-next / claim-batch。
- complete / fail / release。
- claim token 防止旧任务误归还。
- lease 与 heartbeat。
- stale task 释放。
- expired lease 释放。
- sweep / audit / maintenance。
- 生成 JSON 或 Markdown 上下文。
- 输出 prompt 文件，方便外部 CLI Agent 审计。
- 归还外部 Artifact。
- 记录 TaskAssignment transition history。

常用命令：

```powershell
python -m app.task_center list --project-root <project-root>
python -m app.task_center summary --project-root <project-root>
python -m app.task_center context <assignment-id> --project-root <project-root> --format markdown
python -m app.task_center claim-next --project-root <project-root> --agent-id <agent-id> --with-context
python -m app.task_center claim-for-agent <agent-id> --project-root <project-root> --with-context
python -m app.task_center claim-for-agent <agent-id> --project-root <project-root> --with-context --prompt-file .conductor/task_center/prompts/agent-task.md
python -m app.task_center complete <assignment-id> --project-root <project-root> --claim-token <token> --result-summary "done"
python -m app.task_center fail <assignment-id> --project-root <project-root> --claim-token <token> --blocked-reason "reason"
python -m app.task_center release-stale --project-root <project-root> --stale-after-seconds 3600
python -m app.task_center maintenance --project-root <workspace-root> --fail-on-findings
python -m app.task_center maintenance-status --project-root <workspace-root> --fail-on-findings
python -m app.task_center watchdog --project-root <workspace-root> --max-age-seconds 7200 --fail-on-unhealthy
```

对动态激活的 Agent，推荐使用 `claim-for-agent --with-context --prompt-file`。这会在一次受审计的状态转换里完成任务领取、claim token 返回、匹配 Agent 席位记录、上下文渲染和 Markdown prompt 落盘，适合交给外部 CLI coding agent 直接执行。

Board API 同样支持动态 Agent 领取任务：

```text
GET  /api/projects/{project_id}/tasks/{assignment_id}/agents
GET  /api/projects/{project_id}/agents/{agent_id}/tasks
POST /api/projects/{project_id}/agents/{agent_id}/claim-task
POST /api/projects/{project_id}/tasks/claim-batch
POST /api/projects/{project_id}/tasks/sweep
```

Task Center 是后续实现“外部 coding agent 从任务中心领任务，完成后归还状态和产物”的基础。

## 9. Artifact、日志与 Manifest

### 9.1 Artifact

Artifact 是 Agent 或 Harness 的交付记录。关键字段包括：

- `id`
- `workitem_id`
- `agent_id`
- `kind`
- `title`
- `content`
- `source_backend`
- `path`
- `version`
- `parent_artifact_id`
- `derived_from`
- `review_of`
- `collaboration_session_id`

重要 Artifact 类型：

- `requirement_spec`
- `frozen_requirement_spec`
- `design_doc`
- `frozen_design_spec`
- `code_change`
- `test_report`
- `review`
- `revision`
- `delivery_report`

### 9.2 Event Log

Event Log 用来记录项目执行过程中的关键事件，例如：

- 项目创建。
- 阶段推进。
- WorkItem 创建和完成。
- Agent 激活。
- CLI / LLM 执行。
- 质量门禁结果。
- blocker。
- retry / rework。

### 9.3 Run Manifest

Run Manifest 是每次运行的审计清单，记录：

- project id
- project root
- run profile
- final status
- agents
- executions
- cli runs
- llm runs
- collaboration runs
- workitems
- artifacts
- artifact files
- task prompt files
- task assignments
- preflight gate
- retry history
- scope contract results
- delivery readiness
- log path
- report path

Manifest 是后续 resume、replay、audit、benchmark 和任务中心持久化的基础。

## 10. 配置与环境

### 10.1 LLM 配置

本地 LLM 配置文件：

```text
.conductor/llm.config.json
```

该文件应被 `.gitignore` 忽略。页面或 CLI 保存配置时，不应把真实 API key 打印到日志或 Manifest。

### 10.2 推荐测试目录

真实落地测试项目建议统一放在：

```text
C:\99_self\conductor_test\<case_name>
```

不要把临时测试项目散落在 `C:\99_self` 根目录。

### 10.3 Windows 中文编码

平台已经做了部分 UTF-8 处理：

- CLI 入口配置 UTF-8 stdio。
- 子进程注入 `PYTHONUTF8=1`。
- 子进程注入 `PYTHONIOENCODING=utf-8`。
- ShellHarness / StaticWebHarness 使用 UTF-8 环境。
- `.editorconfig` 固定仓库文本 UTF-8。

如果 PowerShell 显示乱码，优先确认文件本身是否是 UTF-8，而不是只看终端显示。可使用：

```powershell
Get-Content -Encoding utf8 <file>
```

## 11. 运行方式

### 11.1 安装依赖

```powershell
cd C:\99_self\conductor\conductor-ai
pip install -r requirements.txt
```

如果使用虚拟环境或 conda，先激活环境后再运行命令。

### 11.2 运行测试

```powershell
python -m pytest -q
```

### 11.3 启动一个项目

```powershell
python -m app.run_project `
  --project-root C:\99_self\conductor_test\reading_list `
  --requirement "做一个阅读清单应用，支持新增、删除、筛选和导出"
```

### 11.4 恢复已有项目

```powershell
python -m app.run_project `
  --project-root C:\99_self\conductor_test\reading_list `
  --resume-project-id <project-id>
```

释放超时任务后恢复：

```powershell
python -m app.run_project `
  --project-root C:\99_self\conductor_test\reading_list `
  --resume-project-id <project-id> `
  --release-stale-tasks `
  --stale-after-seconds 3600
```

### 11.5 运行诊断

```powershell
python -m app.diagnostics
python -m app.diagnostics --probe-cli
python -m app.diagnostics --preflight-llm
python -m app.run_project --diagnose
python -m app.run_project --diagnose --diagnose-cli --diagnose-llm
```

诊断输出会包含 CLI 可用性、角色绑定、LLM server/model/context window、LLM timeout 健康状态、UTF-8 编码就绪度，以及最近一次 preflight gate 摘要。低 timeout 会作为 `timeout_status=low` 返回建议；非法 timeout 会阻断 LLM model probe，避免在配置明显错误时继续发起真实请求。

### 11.6 需求阶段 Benchmark

```powershell
python -m app.requirement_benchmark run-suite `
  --output-dir C:\tmp\conductor_requirement_suite `
  --platform-llm local `
  --direct-llm local `
  --direct-prompt-mode plain `
  --llm-base-url http://127.0.0.1:1234/v1 `
  --llm-model qwen2.5-coder-14b-instruct `
  --llm-timeout 180 `
  --max-steps 4 `
  --collaboration-max-rounds 1 `
  --static-requirement-review
```

该 benchmark 用于比较：

- 模型直接输出需求文档。
- 模型通过 Conductor 需求协作流程输出需求文档。

当前验证结论是：在一般模型或本地模型上，平台化流程通常能明显提高需求规格完整性、边界清晰度和可测试性。

## 12. Board 与前端

平台有 Board 后端和前端探索，但当前优先级低于后端编排、日志、Manifest、Task Center 和端到端真实执行。

Board 当前适合展示：

- 项目列表。
- 当前阶段。
- 当前 Agent。
- 当前 WorkItem。
- Artifact。
- 运行上下文。
- Task Center 状态。
- Collaboration team plan。
- Event log。
- Preflight / audit 状态。

后续如果要重做前端，建议前端只依赖后端接口和 Board snapshot，不要把流程逻辑写进页面。

## 12.1 TL 动态团队规划

TL Agent 会在阶段开始、运行时失败、反馈返工等节点生成 `agent_team_plans`，并把动态 Agent 激活记录持久化到 state、Manifest 和 Board snapshot。当前策略已经覆盖：

- UI/API/data 等需求特征触发的前端、后端、测试和设计席位拆分。
- 运行时失败或 retry 触发的 `failure_triage` / `release_risk` 复核席位。
- 历史角色失败率触发的独立质量复核。
- 缺失 testing checklist evidence 的返工触发 `rework_acceptance_guard`。
- 带 required evidence 的 testing checklist 触发 `evidence_trace_guard`，专门审计测试证据是否逐条覆盖。
- 前后端并行实现触发 `integration_contract_guard`，由 solution designer 复核 API/UI/data 契约和交接边界。

并行开发席位必须声明 `write_scope`。Run Manifest Verifier 会拒绝同一个 `agent_team_plan` 内重叠的 `parallel_development` 写入范围，作为外部 CLI Agent 并行领取任务前后的冲突审计边界。

Run Manifest Verifier 也会检查 Development WorkItem 的 handoff 约束：如果开发任务引用冻结需求或冻结设计输入，却没有在 `acceptance_criteria` 中显式要求保持需求/设计基线，会产生 warning。

Task Center context 会把每个 eligible dynamic Agent 的 `claimable_for_agent` 和 `write_scope_conflict_assignment_ids` 写入 JSON 与 Markdown prompt。外部 CLI Agent 在 prompt 里看到 `claimable_for_agent=false` 时，应先等待或释放冲突 assignment，而不是直接开始修改文件。

Context 顶层还会输出 `handoff_safety`，汇总 `ready_for_handoff`、依赖阻塞、写入范围冲突、warnings 和 guidance；其中 `baseline_handoff` 会标记 Development 任务引用的冻结需求/设计基线是否已经进入 `acceptance_criteria`。Markdown prompt 同步渲染 `## Handoff Safety`，让外部 worker 在领取和开工前就能判断当前任务是否安全。

Task Center CLI 的预期失败会在 stderr 输出机器可读 JSON，包含 `error_code`、`status_code` 和可选 `details`。例如写入范围冲突会返回 `error_code=write_scope_conflict`，并在 `details.write_scope_conflict_assignment_ids` 中列出阻塞当前领取的已声明 assignment。

## 13. Human Control

Human Control 是人类接管与审批入口。它不替代 Controller，而是在 Controller 推进前插入显式 hold、approval 或 override 记录。

Human Control actions 会写入 state、Board snapshot、Run Manifest 和 Markdown 项目报告，因此 pause、approval、reject、override 决策在交付后仍可审计。

当前能力：

- `pause`：暂停自动推进。
- `resume`：恢复自动推进。
- `request-approval`：请求人类审批某个 controller action。
- `approve`：批准当前 gate，并默认继承待审批 gate 的 payload。
- `reject`：拒绝当前 gate，项目继续保持 human hold。
- `override`：记录人类 override。
- `status`：查看当前是否存在 active hold。

常用命令：

```powershell
python -m app.human_control status --project-root <project-root>
python -m app.human_control pause --project-root <project-root> --actor operator --reason "inspect delivery"
python -m app.human_control resume --project-root <project-root> --actor operator --reason "continue"
python -m app.human_control request-approval --project-root <project-root> --actor tl_agent --reason "high risk escalation" --controller-action escalate_project --stage testing
python -m app.human_control approve --project-root <project-root> --actor operator --reason "approved after review"
python -m app.human_control reject --project-root <project-root> --actor operator --reason "needs correction"
python -m app.human_control override --project-root <project-root> --actor operator --reason "manual override" --controller-action escalate_project --stage testing
```

Board API 也提供同等控制入口：

```text
GET  /api/projects/{project_id}/human-control
POST /api/projects/{project_id}/human-control/pause
POST /api/projects/{project_id}/human-control/resume
POST /api/projects/{project_id}/human-control/request-approval
POST /api/projects/{project_id}/human-control/approve
POST /api/projects/{project_id}/human-control/reject
POST /api/projects/{project_id}/human-control/override
```

当状态目录中有多个项目时，必须额外传入：

```powershell
--project-id <project-id>
```

当前限制：

- 已有 CLI 和 Board API 控制面，Board 页面按钮还未完善。
- 审批策略仍然很轻量，后续应接入 TL Agent 风险判断、审批模板和人类审查记录。

## 14. 当前完成度判断

### 已较成熟

- 需求阶段多 Agent 协作雏形。
- 需求冻结与质量门禁。
- 需求 benchmark。
- Artifact 与 Manifest 审计。
- Task Center 基础协议。
- 失败恢复与重试基础策略。
- LLMHarness OpenAI-compatible 接入。
- CLI Agent 扫描与绑定基础。
- 静态 Web 小项目验证。

### 仍需打磨

- 动态 Agent 创建仍偏规则驱动，不是完全自主实时规划。
- TL Agent 全局把控还不完整。
- 人类随时接管、审批和 override 已有 CLI 与 Board API 控制面，但 Board 页面交互和审批策略还不是完整产品能力。
- 并行开发 Agent 的冲突控制仍弱。
- 开发和测试阶段还没有达到需求阶段同等级质量闭环。
- Board 可视化不是当前最强部分。
- 长周期项目恢复、回放、跨多次运行协作仍需更多真实项目验证。

## 15. 近期建议路线

建议下一步按以下顺序推进：

1. 强化 frozen requirement 到 design / development / testing 的上下文传递。
2. 把 design 阶段做成与 requirement 阶段同等级的多 Agent 质量闭环。
3. 完善 Task Center 与动态 Agent 的领取协议，让外部 CLI Agent 可以稳定领任务、写代码、归还 Artifact。
4. 做小型静态 Web 项目端到端真实闭环：需求冻结、设计冻结、实现、测试、返工、Manifest 归档。
5. 增强 TL Agent：全局风险判断、团队扩缩容建议、阶段推进裁决、人类接管点。
6. 建立真实案例 benchmark：平台流程 vs 直接 CLI / 直接模型，重点测试一般模型和本地模型。

## 16. 当前平台一句话状态

Conductor 目前已经不是最小 demo，而是一个具备真实项目执行雏形的多 Agent 编排平台；它最强的部分是需求协作、状态审计、任务中心和可追踪性，下一阶段应把设计、开发、测试闭环提升到同等质量，并补齐 TL Agent、人类接管和并行开发协议。
