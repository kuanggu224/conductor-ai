# Conductor 平台说明文档

更新时间：2026-05-19

## 1. 平台定位

Conductor 是一个 AI Native 的 Project Execution System。它不是普通聊天工具，也不是单纯把多个 Agent 串起来的调度器。平台目标是让 AI 以软件团队的方式，从需求理解、方案设计、任务拆解、协作执行、质量验证到交付归档，持续推进一个项目。

当前阶段的重点不是追求复杂自治，而是先把项目交付流程做成可控、可追踪、可恢复、可验证的工程系统。

一句话定义：

> Conductor 是一个由 Controller 控制流程、由多 Agent 承担职责、由 Shared State 记录事实、由 Manifest 审计交付过程的 AI 项目执行平台。

## 2. 核心设计原则

### 2.1 项目驱动

平台以 `Project` 为核心对象，而不是以一次对话或一个 prompt 为核心。每个项目都有目标、阶段、工作项、Agent、Artifact、日志和运行清单。

### 2.2 Controller 控制流程

Agent 不直接决定项目是否进入下一阶段。流程推进由 `LeadController` 和共享状态控制，避免 Agent 自由发挥导致流程失控。

### 2.3 Shared State 作为事实源

项目状态集中记录在 `SharedProjectState` 中，包括当前阶段、WorkItem、Artifact、Agent 激活记录、协作记录、失败信息和事件日志。

### 2.4 Agent 是职责，不等于模型

Agent 表示角色、任务边界、产出契约和协作规则。模型只是执行后端，可以是云端 LLM、本地模型、Agent CLI、Shell Harness 或 mock fallback。

### 2.5 可观测与可审计优先

每次执行都应留下可审计证据，包括项目日志、Artifact、Manifest、Report、Preflight 结果、Replay Trace 和 Verification 报告。

## 3. 当前主流程

默认工作流为：

```text
requirement -> design -> development -> testing
```

### 3.1 Requirement 阶段

目标是把用户自然语言需求转成冻结需求规格。

当前能力：

- 创建 `requirement_spec` WorkItem。
- 通过多 Agent 需求评审完善需求。
- 支持 `requirement_designer`、`designer`、`solution_designer`、`backend_engineer`、`frontend_engineer`、`tester` 等角色参与评审。
- 支持动态评审席位规划，例如交互设计、信息架构、流程、安全边界、接口契约、边界用例等视角。
- 通过需求质量门禁后生成 `frozen_requirement_spec`。
- 后续设计、开发、测试必须以冻结需求为基线。
- 若需求协作或质量门禁失败，会自动生成需求返工 WorkItem。
- 返工有上限，连续失败会阻塞项目，避免无限循环。

### 3.2 Design 阶段

目标是基于冻结需求形成实现方案、页面方案、接口方案或测试方案。

当前设计阶段仍在逐步增强中，已有多 Agent 协作和 Artifact 归档能力，但产品级闭环弱于 Requirement 阶段。

### 3.3 Development 阶段

目标是由后端、前端或其他开发 Agent 执行真实代码任务。

当前能力：

- 可通过 Agent CLI 或 LLMHarness 执行代码/文档类任务。
- 支持 Codex CLI、Claude、Qwen、OpenCode、AspireCode、Aider、Gemini 等 CLI 的扫描和绑定。
- 支持后端/前端 Agent 根据 WorkItem 类型接收任务。
- 支持失败分类、重试和失败后生成可执行返工 WorkItem。
- 开发失败重试耗尽后，可将失败转成同阶段 rework task，保留失败证据和关联 Artifact。

### 3.4 Testing 阶段

目标是验证交付物是否满足冻结需求。

当前能力：

- 支持 ShellHarness 和 StaticWebHarness。
- 支持静态 Web 项目验证，例如页面打开、JS/CSS 资源、基础交互、表单、筛选、导出、删除、按需 CSV 导入等。
- 支持 Requirement Coverage 检查。
- 支持测试失败反馈生成开发返工任务。
- 支持项目报告和 Manifest 中记录验证结果、失败原因和修复建议。

## 4. 主要模块

```text
app/
  run_project.py              项目运行 CLI 入口
  board.py                    Web Board 后端入口
  requirement_benchmark.py    需求阶段评分与平台对比 CLI
  verify_manifest.py          Manifest 自检入口
  replay_manifest.py          Manifest 回放入口
  task_center.py              Task Center CLI

conductor/
  controller/                 LeadController 和 Engine
  domain/                     Project、WorkItem、Execution、Artifact 等核心模型
  workflow/                   默认阶段模板
  execution/                  Runner、Planner、失败策略、运行流
  collaboration/              多 Agent 协作评审
  agents/                     Agent、Profile、CLI executor、LLM backend
  harness/                    ShellHarness、LLMHarness、StaticWebHarness
  context/                    ContextBuilder
  state/                      内存和文件状态存储
  artifacts/                  Artifact 写入、读取、范围契约
  task_center/                任务领取、归还、上下文协议
  board/                      Board snapshot 服务
  config/                     LLM、CLI、运行 profile、系统配置
  manifest.py                 Run Manifest 生成
  replay_verifier.py          Manifest verifier
```

## 5. 多 Agent 协作机制

Conductor 的多 Agent 不是多人聊天，而是结构化职责协作。

当前协作模型：

- Lead Agent 产出初稿。
- Reviewer Agent 在固定阶段提出审阅意见。
- Lead Agent 统一吸收 reviewer 意见后修订。
- 可以配置最大审阅轮次。
- 同一轮中先收集全部 reviewer 意见，再由 lead 统一修订。
- 协作过程会记录 draft versions、review contributions、review decision、team plan 和 final artifact。

需求阶段已经支持更细粒度的动态评审席位，例如：

- `designer.interaction`
- `designer.information_architecture`
- `solution_designer.process`
- `solution_designer.security_boundary`
- `backend_engineer.contracts`
- `frontend_engineer.states`
- `tester.edge_cases`

当前限制：

- 动态 Agent 创建还不是完全由 LLM 实时自由规划。
- 目前更接近“规则和 TL 决策共同控制的按需激活”。
- 并行开发协作仍处在早期阶段。

## 6. 执行后端

### 6.1 LLMHarness

LLMHarness 用于调用 OpenAI-compatible API，生成受控文本或代码 Artifact。

已验证后端：

- LM Studio 本地模型。
- 九天云端 `jiutian-lan-comv3`。

九天配置文件：

```text
.conductor/llm.config.json
```

关键配置：

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

API key 只保存在本地配置文件中，不应提交。

### 6.2 Agent CLI

Agent CLI 用于让外部 Coding Agent 真实修改项目文件。

当前支持扫描和绑定：

- `codex`
- `claude`
- `qwen`
- `opencode`
- `aspirecode`
- `aider`
- `gemini`

### 6.3 ShellHarness

ShellHarness 用于执行受控 shell 命令，例如测试命令、静态验证命令等。

### 6.4 StaticWebHarness

StaticWebHarness 用于验证小型静态 Web 项目，包括 HTML、JS、CSS、浏览器 smoke 和基本交互。当前 static web delivery 会在冻结需求明确要求 import/upload 时生成 CSV 导入控件；Harness 会分别验证导入样本处理和导出下载，避免 `Import CSV` 抢占 export/download 证据。

## 7. Task Center

Task Center 是给外部 Agent 或人类 worker 使用的轻量任务中心。

它提供：

- 查看可领取任务。
- claim / claim-next。
- complete / fail / release。
- stale claimed task 释放。
- 任务上下文生成。
- prompt 文件归档。
- 外部 worker 归还 Artifact。

它当前不是完整分布式队列，但已经提供了稳定的文件状态边界和任务归还协议。

常用命令：

```powershell
python -m app.task_center list --project-root <project-root>
python -m app.task_center summary --project-root <project-root>
python -m app.task_center claim-next --project-root <project-root> --agent-id <agent-id> --with-context
python -m app.task_center complete <assignment-id> --project-root <project-root> --result-summary "done"
python -m app.task_center fail <assignment-id> --project-root <project-root> --blocked-reason "reason"
python -m app.task_center release-stale --project-root <project-root> --stale-after-seconds 3600
```

## 8. Artifact、日志与 Manifest

### 8.1 Artifact

Artifact 是 Agent 或 Harness 的产出记录。它包含：

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

Artifact 用于支撑上下文构建、需求冻结、审计、回放和后续任务交接。

### 8.2 Event Log

项目事件会写入 `.conductor/logs` 或 `.conductor_logs`，用于追踪项目推进、Agent 激活、WorkItem 执行、失败和阶段切换。

### 8.3 Run Manifest

Run Manifest 是一次项目运行的审计清单。它记录：

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
- artifacts
- artifact files
- task prompt files
- retry history
- requirement evaluations
- scope contract results
- delivery readiness
- preflight gate
- report path
- log path

最新 manifest summary 还会记录：

- `llm_models`
- `llm_source_backends`
- `llm_run_count`
- `llm_token_usage`
- `llm_cost_estimate`
- `requirement_quality_score`
- `delivery_readiness_status`
- `scope_contract_status`

如果要确认某次运行是否真实使用九天，查看 manifest 中：

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

用于确认本地或云端模型是否可用。

```powershell
python -m app.requirement_benchmark preflight --backend cloud --output-dir .conductor\diagnostics\jiutian-preflight
```

输出：

```text
cloud.preflight.txt
cloud.preflight.json
```

JSON 中记录：

- backend
- success
- model
- base_url
- duration_ms
- error
- content

不会记录 API key。

### 9.2 Requirement Benchmark

用于对比平台工作流产物和模型直出产物。

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

用于检查 manifest 自洽性。

```powershell
python -m app.verify_manifest <manifest-path>
```

Manifest verifier 会检查：

- schema 基本结构
- summary 计数
- WorkItem / Execution / Artifact / Agent 引用关系
- 文件索引
- API key / token 泄露风险
- LLM run 是否记录真实模型名
- preflight gate 摘要一致性
- retry history 和 resume cursor

### 9.4 Replay Trace

用于从 manifest 生成只读回放，不重新执行 Agent。

```powershell
python -m app.replay_manifest <manifest-path> --format markdown
```

## 10. 常用运行方式

### 10.1 运行测试

```powershell
python -m pytest -q
```

最近一次全量验证：

```text
575 passed in 61.14s
```

### 10.2 创建并运行项目

```powershell
python -m app.run_project --project-root C:\path\to\project --requirement "Build a reading list"
```

### 10.3 恢复已有项目

```powershell
python -m app.run_project --project-root C:\path\to\project --resume-project-id <project-id>
```

### 10.4 只做运行前检查

```powershell
python -m app.run_project --project-root C:\path\to\project --preflight-only
```

### 10.5 写出审计包

```powershell
python -m app.run_project `
  --requirement-file requirement.txt `
  --project-root C:\path\to\project `
  --write-audit-bundle
```

## 11. 当前完成度判断

### 已相对稳定

- 项目状态模型。
- Requirement 阶段产品级闭环。
- 多 Agent 需求评审。
- LLMHarness 接入。
- 九天云端接入和可审计验证。
- Task Center 基础协议。
- Manifest、Verifier、Replay、Audit Bundle。
- 失败分类、重试和部分返工机制。
- 静态 Web 验收基础能力。

### 仍需加强

- Design 阶段需要达到 Requirement 阶段同等质量。
- Development 阶段需要更多真实代码闭环案例。
- Testing 阶段需要更强的项目类型覆盖。
- 动态 Agent 创建还不够智能，更多是规则驱动的按需激活。
- 多 Agent 并行开发还未形成稳定生产级协议。
- Web Board 暂时不是当前重点，日志和 Manifest 更适合现阶段开发调试。
- 长周期运行、暂停、人工接管和恢复策略还需要继续打磨。

## 12. 当前推荐开发路线

1. 巩固 Requirement 到 Design 的上下文传递。
2. 将 Design 阶段做成同样有门禁、有评审、有冻结产物的闭环。
3. 选择一个小型静态 Web 项目做完整端到端：需求、设计、开发、测试、返工、归档。
4. 强化 Task Center，让外部 Coding Agent 可以稳定领取和归还任务。
5. 增强动态 Agent 规划，让 TL Agent 根据项目复杂度实时生成更多同职位 Agent。
6. 将失败恢复扩展到更多阶段和更多失败类型。
7. 最后再重新推进 Web Board 和可视化。

## 13. 使用注意事项

- `.conductor/llm.config.json` 包含本地 API key，不要提交。
- Windows PowerShell 可能显示中文乱码，应优先用 UTF-8 文件和 Python 读取确认内容。
- 真实模型验证不要每改一个小功能就跑完整链路，应批量修改后集中验证。
- 现阶段如需判断质量，优先看 Artifact、Manifest、Verifier、Benchmark 报告，而不是只看 Board 页面。
- mock artifact 只适合开发期验证，不应作为真实交付质量依据。
