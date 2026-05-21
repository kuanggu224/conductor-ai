# Conductor 平台说明文档

更新时间：2026-05-20

## 1. 平台定位

Conductor 是一个 AI Native 的 Project Execution System。它不是普通聊天工具，也不是简单的多 Agent 调度器，而是把 AI 组织成一个可控的软件项目团队，用结构化流程推进项目交付。

平台核心目标：

- 将自然语言需求转成可冻结、可追踪、可验证的项目基线。
- 通过多 Agent 职责分工完成需求、设计、开发、测试和验收。
- 由 Controller 控制流程推进，避免 Agent 自由发挥导致项目失控。
- 用 Shared State、Artifact、Event Log、Manifest 留下完整审计证据。
- 放大小模型、本地模型和 CLI Agent 的项目执行能力，而不是只依赖顶级模型单次生成。

一句话定义：

> Conductor 是一个由 Controller 控制流程、由多 Agent 承担职责、由 Shared State 记录事实、由 Manifest 审计交付过程的 AI 项目执行平台。

## 2. 核心原则

### 2.1 Project First

平台以 `Project` 为核心对象，而不是以一次对话或一个 prompt 为核心。每个项目都有目标、阶段、WorkItem、Agent、Artifact、日志、状态和运行清单。

### 2.2 Controller Owns Flow

Agent 不直接决定项目是否进入下一阶段。流程推进由 `LeadController` 和共享状态控制，Agent 只负责在约束内完成分配的任务。

### 2.3 Shared State Is Source of Truth

项目状态集中记录在 `SharedProjectState` 中，包括当前阶段、WorkItem、Artifact、Agent 激活、协作记录、失败信息、Task Assignment 和事件日志。

### 2.4 Agent Is Role, Backend Is Tool

Agent 表示角色、职责、产出契约和协作规则。执行后端可以是：

- 云端 OpenAI-compatible API
- LM Studio 本地模型
- Codex CLI
- Claude Code CLI
- OpenCode / AspireCode
- Qwen CLI
- Aider / Gemini
- ShellHarness
- StaticWebHarness
- Mock fallback

### 2.5 Auditability First

每次执行都应可追踪。平台会持续记录：

- Artifact
- Event Log
- Run Manifest
- LLM Runs
- CLI Runs
- Task Assignment transition history
- Retry / Rework history
- Verification report
- Delivery readiness
- Requirement / Design quality score

## 3. 默认项目流程

默认工作流：

```text
requirement -> design -> development -> testing
```

### 3.1 Requirement 阶段

目标：把用户自然语言需求转成冻结需求规格。

当前能力：

- 创建 `requirement_spec` WorkItem。
- 多 Agent 参与需求评审和修订。
- 支持静态评审团队和动态评审席位。
- 需求通过质量门禁后生成 `frozen_requirement_spec`。
- 后续设计、开发、测试必须以冻结需求为基线。
- 需求质量不足时自动创建需求返工 WorkItem。
- 返工有上限，连续失败会阻塞项目并记录 blocker。

典型动态评审席位：

- `designer.interaction`
- `designer.information_architecture`
- `solution_designer.process`
- `solution_designer.security_boundary`
- `backend_engineer.contracts`
- `frontend_engineer.states`
- `tester.edge_cases`

### 3.2 Design 阶段

目标：基于冻结需求形成设计方案、实现边界和下游交付约束。

当前能力：

- 支持设计阶段多 Agent 协作评审。
- 设计通过质量门禁后生成 `frozen_design_spec`。
- ContextBuilder 会优先读取冻结设计。
- Development、Testing、Task Center、Manifest、Delivery Readiness 均能识别冻结设计基线。
- 设计协作或质量门禁失败会自动生成设计返工 WorkItem。
- 连续设计返工失败会阻塞项目。

当前限制：

- 设计阶段已经有产品级雏形，但真实项目类型覆盖仍少于 Requirement 阶段。

### 3.3 Development 阶段

目标：由后端、前端或其他开发 Agent 执行真实代码任务。

当前能力：

- Agent 可通过 CLI 或 LLMHarness 执行任务。
- 支持按 WorkItem 类型分配 backend/frontend/tester 等角色。
- 支持失败分类、重试、返工 WorkItem。
- 支持 Task Center 外部领取任务并归还 Artifact。
- 支持静态 Web 小项目端到端验证。

当前限制：

- 多 Agent 并行开发还不是生产级协议。
- 复杂代码冲突、跨文件协作、长期分支管理仍需增强。

### 3.4 Testing 阶段

目标：验证交付物是否满足冻结需求和冻结设计。

当前能力：

- ShellHarness 执行命令级测试。
- StaticWebHarness 验证静态 Web 项目。
- Requirement coverage 检查。
- 测试失败可反馈生成开发返工任务。
- Manifest 记录测试结果、失败原因和修复建议。

当前限制：

- 项目类型覆盖仍需扩展到更多真实应用场景。

## 4. 主要模块

```text
app/
  run_project.py              项目运行入口
  task_center.py              Task Center CLI
  requirement_benchmark.py    需求阶段 benchmark
  verify_manifest.py          Manifest 校验
  replay_manifest.py          Manifest 回放
  board.py                    Web Board 后端入口

conductor/
  controller/                 LeadController 和 Engine
  workflow/                   默认阶段模板
  domain/                     Project / WorkItem / Artifact / Assignment 等模型
  collaboration/              多 Agent 协作评审
  execution/                  Runner / Planner / Retry / Failure policy
  agents/                     Agent profile / CLI executor / LLM backend
  harness/                    ShellHarness / LLMHarness / StaticWebHarness
  context/                    ContextBuilder
  artifacts/                  Artifact 存储和契约
  state/                      内存和文件状态存储
  task_center/                任务领取、上下文、归还协议
  board/                      Board snapshot 服务
  config/                     LLM / CLI / Profile 配置
  manifest.py                 Run Manifest
  replay_verifier.py          Manifest verifier
```

## 5. 多 Agent 协作模型

Conductor 的多 Agent 不是多人闲聊，而是结构化职责协作。

当前协作流程：

1. Lead Agent 产出初稿。
2. Reviewer Agents 从各自职责视角提出评审意见。
3. 同一轮先收集全部 reviewer 意见。
4. Lead Agent 统一吸收意见并修订。
5. Controller 根据质量门禁和评审意见覆盖情况裁决是否接受。
6. 通过后冻结 Artifact，失败则生成返工任务。

已经实现：

- 多角色评审。
- 同职位多评审席位。
- Team Plan 记录。
- Collaboration Runs 进入 Manifest。
- Board Snapshot 暴露协作信息。
- 需求阶段动态评审席位规划。

仍需完善：

- 更智能的实时 Agent 规划。
- TL Agent 基于历史表现和风险动态扩缩团队。
- 多 Agent 并行开发的冲突控制和归并协议。
- 人类随时接管与审批工作流。

当前 TL 动态规划已经能识别运行时失败、历史角色失败率，以及带有缺失 testing checklist evidence 的开发返工任务。对于这类返工，TL 会追加 `rework_acceptance_guard` tester 席位，专门检查返工是否补齐缺失验收证据和回归风险。对于同时拆出前端和后端并行开发的复杂实现，TL 会追加 `integration_contract_guard` solution_designer 席位，提前复核 API/UI/data 契约、校验边界和交接风险，避免并行 agent 各自实现后在集成阶段才暴露冲突。

并行开发席位必须声明 `write_scope`。Run Manifest Verifier 会校验同一个 `agent_team_plan` 内的 `parallel_development` 席位写入范围不能重叠；如果两个并行 Agent 声称写同一 scope，审计会直接失败，避免把冲突留到归并阶段才发现。

## 6. 执行后端

### 6.1 LLMHarness

用于调用 OpenAI-compatible API，生成受控文本或代码 Artifact。

已验证后端：

- LM Studio 本地模型
- 九天云端模型 `jiutian-lan-comv3`

九天配置文件：

```text
.conductor/llm.config.json
```

关键配置示例：

```json
{
  "cloud": {
    "cloud_llm_base_url": "https://jiutian.10086.cn/largemodel/moma/api/v3",
    "cloud_llm_model": "jiutian-lan-comv3",
    "cloud_llm_api_key": "<local-only>",
    "cloud_llm_timeout": 120.0,
    "cloud_llm_enabled": true
  },
  "usage": {
    "runner_enabled": true,
    "preferred_backend": "cloud"
  }
}
```

注意：API key 只应保存在本地配置文件，不应提交到仓库。

### 6.2 Agent CLI

用于让外部 Coding Agent 真实修改项目文件。

当前支持扫描和绑定：

- `codex`
- `claude`
- `qwen`
- `opencode`
- `aspirecode`
- `aider`
- `gemini`

### 6.3 ShellHarness

用于执行受控 shell 命令，例如测试命令、构建命令、验证命令。

### 6.4 StaticWebHarness

用于验证小型静态 Web 项目，包括 HTML、JS、CSS、页面打开、基础交互和 smoke check。
当前可审计交互证据包括表单提交、localStorage 刷新保留、筛选/搜索、删除、导出下载，以及文件导入/上传样本处理。文件处理类需求会映射到 `file_import` testing checklist，并要求 `Browser file import processed sample file` 证据；`static_web_delivery` 只会在冻结需求要求 import/upload 时生成真实 CSV 导入控件，避免把未请求的导入能力扩进交付范围。后端/API 类需求会映射到 `api_behavior` testing checklist；`api_validation` WorkItem 的真实验证通过后会记录 `API validation exercised endpoint behavior` 作为接口行为证据，避免 API mock / 后端验收只停留在泛化的 `pytest passed`。当测试失败回流为开发返工时，缺失的 testing checklist evidence 会进入返工 WorkItem 的 acceptance criteria，并出现在 Task Center rework prompt 中，外部 worker 可以直接看到需要补齐哪条验收证据。

## 7. Task Center

Task Center 是给外部 Agent 或人类 worker 使用的轻量任务中心。它不是完整分布式队列，但已经提供清晰的文件状态边界和任务归还协议。

核心能力：

- 查看可领取任务。
- `claim` / `claim-next` / `claim-batch`。
- `complete` / `fail` / `release`。
- claim token 防止错误归还。
- 显式 lease：`lease_seconds` / `lease_expires_at`。
- heartbeat 续租。
- 释放 stale claimed task。
- 释放 expired lease task。
- `sweep` 单项目维护。
- `sweep-all` 多项目维护。
- `audit` 非破坏性检查任务完整性。
- `audit-all` 多项目非破坏性审计，适合定时维护后统一发现异常。
- `maintenance` 多项目维护闭环，按顺序执行 `sweep-all` 和 `audit-all` 并输出统一报告。
- 生成任务上下文和 prompt 文件。
- 归还外部 Artifact。
- 记录 TaskAssignment transition history。

常用命令：

```powershell
python -m app.task_center list --project-root <project-root>
python -m app.task_center summary --project-root <project-root>
python -m app.task_center audit --project-root <project-root> --fail-on-findings
python -m app.task_center agents-for <assignment-id> --project-root <project-root>
python -m app.task_center tasks-for-agent <agent-id> --project-root <project-root> --claimable-only
python -m app.task_center claim-for-agent <agent-id> --project-root <project-root>
python -m app.task_center claim-next --project-root <project-root> --agent-id <agent-id> --with-context
python -m app.task_center claim-batch --project-root <project-root> --agent-id <agent-id> --limit 2 --max-limit 5
python -m app.task_center complete <assignment-id> --project-root <project-root> --claim-token <token> --result-summary "done"
python -m app.task_center fail <assignment-id> --project-root <project-root> --claim-token <token> --blocked-reason "reason"
python -m app.task_center release-stale --project-root <project-root> --stale-after-seconds 3600
python -m app.task_center release-expired-leases --project-root <project-root>
python -m app.task_center sweep --project-root <project-root> --stale-after-seconds 3600
python -m app.task_center sweep-all --project-root <workspace-root> --stale-after-seconds 3600
python -m app.task_center audit-all --project-root <workspace-root> --fail-on-findings
python -m app.task_center maintenance --project-root <workspace-root> --fail-on-findings
python -m app.task_center maintenance-status --project-root <workspace-root> --fail-on-findings
python -m app.task_center watchdog --project-root <workspace-root> --max-age-seconds 7200 --fail-on-unhealthy
```

多项目维护报告：

```powershell
python -m app.task_center sweep-all `
  --project-root <workspace-root> `
  --stale-after-seconds 3600 `
  --output .conductor\maintenance\sweep-all.json

python -m app.task_center audit-all `
  --project-root <workspace-root> `
  --fail-on-findings `
  --output .conductor\maintenance\audit-all.json

python -m app.task_center maintenance `
  --project-root <workspace-root> `
  --stale-after-seconds 3600 `
  --fail-on-findings `
  --output .conductor\maintenance\report.json `
  --latest-output .conductor\maintenance\latest.json

python -m app.task_center watchdog `
  --project-root <workspace-root> `
  --latest .conductor\maintenance\latest.json `
  --max-age-seconds 7200 `
  --stale-after-seconds 3600 `
  --output .conductor\maintenance\report.json `
  --latest-output .conductor\maintenance\latest.json `
  --fail-on-unhealthy
```

这些报告适合后续接入 Windows Task Scheduler、cron 或独立守护进程。`sweep-all` 负责清理可恢复的过期任务，`audit-all` 负责发现仍需人工或 Controller 处理的状态异常，`maintenance` 则提供一个可直接定时运行的组合入口。`watchdog` 会先读取 latest 指针，并在 latest 缺失、过期或不健康时自动执行一次 `maintenance`，适合作为调度器或守护进程的单次检查入口；`--check-only` 可用于只读探测。多项目审计和维护报告会聚合 `attention_project_ids`、`finding_code_counts` 和去重后的 `recommendations`，让外部调度器或人类 operator 可以直接看到哪些项目需要处理、主要问题是什么、建议如何修复。`--latest-output` 会写出最近一次维护的轻量摘要，并保留同样的 rollup 字段，便于外部监控或 Board 直接读取最新状态。`maintenance`、latest 指针、`maintenance-status` 和 `watchdog` 还会输出 `operator_guidance` 与 `operator_commands`，提供可复制的定时维护和 watchdog 健康检查命令；旧 latest 文件缺少这些字段时，`maintenance-status` 会按当前参数生成兼容命令。

检查最近一次维护状态：

```powershell
python -m app.task_center maintenance-status `
  --project-root <workspace-root> `
  --latest .conductor\maintenance\latest.json `
  --max-age-seconds 7200 `
  --fail-on-findings
```

`maintenance-status` 会返回 `healthy` 和 `reason` 字段，并透出 latest 指针里的 `attention_project_ids`、`finding_code_counts`、`recommendations`、`operator_guidance` 和 `operator_commands`。常见 reason 包括 `clean`、`findings`、`stale`、`invalid_generated_at` 和 `status_not_clean`。

## 8. Artifact、日志与 Manifest

### 8.1 Artifact

Artifact 是 Agent 或 Harness 的产出记录。关键字段包括：

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
- `design_overview`
- `frozen_design_spec`
- `implementation_report`
- `test_report`
- `external_result`

### 8.2 Event Log

项目事件会写入 `.conductor/logs` 或 `.conductor_logs`，用于追踪项目推进、Agent 激活、WorkItem 执行、失败和阶段切换。

### 8.3 Run Manifest

Run Manifest 是一次项目运行的审计清单。

记录内容包括：

- project id 和 project root
- run profile
- final status
- current stage
- agents
- executions
- cli runs
- llm runs
- collaboration runs
- workitems
- task assignments
- transition history
- lease status
- task center audit findings
- artifacts
- artifact files
- retry history
- requirement evaluations
- design quality score
- scope contract results
- delivery readiness
- preflight gate
- report path
- log path

判断某次运行是否真实使用九天模型，查看：

```text
summary.llm_models
summary.llm_source_backends
llm_runs
```

其中 `summary.llm_models` 应包含：

```text
jiutian-lan-comv3
```

## 9. 诊断与验证

### 9.1 LLM Preflight

```powershell
python -m app.requirement_benchmark preflight `
  --backend cloud `
  --output-dir .conductor\diagnostics\jiutian-preflight
```

输出：

```text
cloud.preflight.txt
cloud.preflight.json
```

不会记录 API key。

### 9.2 Requirement Benchmark

```powershell
python -m app.requirement_benchmark run-suite `
  --cases reading_list `
  --output-dir .conductor\diagnostics\jiutian-reading-list `
  --platform-llm cloud `
  --direct-llm cloud `
  --direct-prompt-mode plain `
  --max-steps 4 `
  --collaboration-max-rounds 1 `
  --static-requirement-review
```

最近一次九天真实验证结果：

```text
platform score: 100
direct score: 42
delta: 58
model: jiutian-lan-comv3
```

### 9.3 Manifest Verification

```powershell
python -m app.verify_manifest <manifest-path>
```

校验内容：

- schema 结构
- summary 计数
- WorkItem / Execution / Artifact / Agent 引用关系
- 文件索引
- API key 泄漏风险
- LLM run 模型记录
- retry history
- resume cursor
- requirement / design quality

### 9.4 Replay

```powershell
python -m app.replay_manifest <manifest-path> --format markdown
```

Replay 只读取 Manifest，不重新执行 Agent。

## 10. 常用运行方式

运行测试：

```powershell
python -m pytest -q
```

最近一次全量验证：

```text
623 passed in 66.38s
```

创建并运行项目：

```powershell
python -m app.run_project `
  --project-root C:\path\to\project `
  --requirement "Build a reading list app"
```

恢复已有项目：

```powershell
python -m app.run_project `
  --project-root C:\path\to\project `
  --resume-project-id <project-id>
```

恢复前执行 Task Center 维护：

```powershell
python -m app.run_project `
  --project-root C:\path\to\project `
  --resume-project-id <project-id> `
  --maintenance-task-center `
  --maintenance-fail-on-findings `
  --maintenance-report-output .conductor\maintenance\pre-run.json `
  --maintenance-latest-output .conductor\maintenance\latest-pre-run.json `
  --stale-after-seconds 3600
```

该入口会在正式推进项目前释放过期 lease 和 stale claim，并把维护后的审计摘要写入输出字段 `pre_run_task_center_maintenance`。如果启用 `--maintenance-fail-on-findings`，维护审计发现错误或警告时会在推进项目前返回退出码 `3`，避免带着坏状态继续运行。`--maintenance-report-output` 会把同一份维护摘要落成 JSON 文件，便于定时任务或外部调度器留存证据；`--maintenance-latest-output` 会写出轻量 latest 指针，便于外部工具读取最近一次恢复前维护状态。

只做运行前检查：

```powershell
python -m app.run_project --project-root C:\path\to\project --preflight-only
```

`app.diagnostics` 和 `run_project --diagnose` 会暴露 CLI 探测、LLM server/model/context window、LLM timeout 健康状态、UTF-8 编码就绪度和最近一次 preflight gate。`timeout_status=low` 用于提醒长 prompt 风险；`timeout_status=invalid` 会阻断 LLM model probe，并在 warnings 中给出明确修复建议。

写出审计包：

```powershell
python -m app.run_project `
  --requirement-file requirement.txt `
  --project-root C:\path\to\project `
  --write-audit-bundle
```

## 11. 当前完成度

相对稳定：

- 项目状态模型。
- Requirement 阶段产品级闭环。
- 多 Agent 需求评审。
- LLMHarness。
- 九天云端模型接入。
- LM Studio 本地模型接入。
- Task Center 基础协议。
- Task Center lease / heartbeat / sweep / sweep-all / audit / audit-all / maintenance。
- Manifest / Verifier / Replay。
- 失败分类、重试和部分返工机制。
- Design frozen baseline。
- Design quality gate。
- Static Web 小型端到端验证。
- Task Center 外部 worker 上下文生成和归还协议。

仍需加强：

- Design 阶段更多真实项目覆盖。
- Development 阶段更多真实代码闭环案例。
- Testing 阶段更多项目类型覆盖。
- 动态 Agent 创建仍偏规则驱动。
- 多 Agent 并行开发协议不够成熟。
- TL Agent 全局策略还不够智能。
- 人类接管、审批、暂停/恢复仍需产品化。
- Web Board 目前不是最优先开发重心，日志和 Manifest 更适合现阶段验证。

## 12. 推荐下一步路线

1. 扩展真实端到端案例，优先覆盖表单流、文件处理流、API mock 流、失败返工流。
2. 强化开发和测试阶段，让它们达到 Requirement 阶段类似的闭环质量。
3. 产品化 Task Center 长周期运行能力，包括维护报告、定时 sweep、恢复策略和守护进程。
4. 增强动态 Agent 规划，让 TL Agent 根据风险、复杂度和历史表现动态扩缩团队。
5. 在后端日志和 Manifest 稳定后，再重新推进 Web Board。

## 13. 使用注意

- `.conductor/llm.config.json` 包含本地 API key，不要提交。
- Windows PowerShell 若显示中文乱码，优先确认文件本身是否为 UTF-8。
- 真实模型验证不建议每次小改都跑完整链路，应批量修改后集中验证。
- 判断质量时优先看 Artifact、Manifest、Verifier、Benchmark 报告，不要只看 Board 页面。
- mock artifact 只适合开发期验证，不应作为真实交付质量依据。
