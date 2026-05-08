# Conductor 平台进度总结

更新时间：2026-05-08

本文档用于把当前 Codex 桌面端对话中的关键上下文沉淀到仓库，方便后续通过 Codex CLI、其他 Agent CLI 或人工继续开发。

平台长期定位参见：`docs/platform_positioning.md`。该文档记录 Conductor 作为 Project Execution System 的北极星目标；本文档记录当前工程进度和交接上下文。

需求环节产品级优化计划参见：`docs/requirement_stage_optimization_plan.md`。

## 1. 当前定位

Conductor 当前不是简单调用一个 CLI 的包装器，而是一个面向多 Agent 软件交付的本地编排平台雏形。

核心目标：

- 以 `Project` 为中心管理一次需求到交付的流程。
- 由 `LeadController` 推进阶段和任务。
- 由 `Runner` 执行 `WorkItem`。
- 由 `SharedProjectState` 作为唯一事实源。
- 由 Artifact、Log、Manifest 沉淀可追踪产物。
- 支持 LLMHarness、Agent CLI、Shell Harness 等多种执行后端。
- 逐步验证“平台工作流是否能提升一般模型/本地模型的交付质量”。

当前平台更偏后端编排和日志可观测能力，前端 Board 暂时不是优先方向。

## 2. 主要目录

```text
app/
  board.py                 Web Board 后端入口
  run_project.py           CLI 项目运行入口

conductor/
  agents/                  Agent、Agent Profile、CLI 执行器
  artifacts/               Artifact 存储与契约
  board/                   Board API 数据服务
  collaboration/           多 Agent 协作评审
  config/                  CLI、LLM、运行 profile、系统配置
  context/                 ContextBuilder
  controller/              Engine 与 LeadController
  domain/                  核心领域模型
  execution/               Planner、Runner、失败策略、运行流
  harness/                 Shell/LLM/Static Web Harness
  logging/                 项目日志
  state/                   内存/文件状态存储
  manifest.py              运行 manifest

tests/                     自动化测试
templates/board.html       当前 Board 页面
```

## 3. 已实现能力

### 3.1 项目与状态

- 支持创建 `Project`，生成阶段、工作项、Agent 激活记录。
- 支持 `requirement -> design -> development -> testing` 主流程。
- 支持 `SharedProjectState` 内存状态与 `FileStateStore` 文件持久化。
- 运行项目时可以指定项目根目录。
- 测试项目统一约定放在 `C:\99_self\conductor_test\<case_name>`。

### 3.2 Task Center 轻量协议

- WorkItem 可以被 Agent 领取、完成、失败或阻塞。
- 状态里已有 `TaskAssignment`。
- 支持 CLI 和 Board API 的 `claim`、`claim-next`、`complete`、`fail`、`release`、`release-stale`。
- 文件版 Task Center 变更会使用项目级 `.lock` 文件并在写入前重新读取 state，降低多进程 worker 重复领取同一任务的风险。
- 文件版 StateStore 启动时会隔离损坏的 `*.state.json` 为 `.corrupt-*`，避免单个坏快照拖垮所有项目加载。
- 支持领取时生成 JSON/Markdown 上下文，包含项目目标、WorkItem、验收标准、输入 artifact 内容和归还协议。
- 支持 `prompt_file` 归档，方便外部 CLI Agent 领取任务后保留可审计 prompt。
- 目前仍是轻量任务中心，不是完整分布式队列。

### 3.3 多 Agent 按需激活

- 已有预设角色：
  - `designer`
  - `requirement_designer`
  - `solution_designer`
  - `backend_engineer`
  - `frontend_engineer`
  - `tester`
- LeadController 会根据当前阶段和 WorkItem 激活需要的 Agent。
- 设计阶段会记录创建了哪些 Agent、为什么创建、对应角色职责。

### 3.4 设计阶段多 Agent 协作

当前设计阶段不再是单一 designer 出稿后直接进入开发。

当前流程：

1. `designer` 产出初稿。
2. `requirement_designer` 与 `solution_designer` 进行设计同侪评审。
3. 若同侪评审要求修改，`designer` 统一修订。
4. `backend_engineer`、`frontend_engineer`、`tester` 进行跨职能评审。
5. 若跨职能评审要求修改，`designer` 再统一修订。
6. 支持配置最大评审轮数。

协作记录包括：

- collaboration session
- review phase
- reviewer decision
- draft versions
- final collaboration artifact
- review/revision 产物文件

### 3.4.1 需求阶段产品级闭环

需求阶段已经从“进入设计前的一段文本”升级为正式阶段：

- `requirement` 阶段生成 `requirement_spec` WorkItem。
- 协作通过后生成 `frozen_requirement_spec`，后续设计、开发、测试必须以它为基线。
- 需求协作或质量门禁失败时，会自动创建新的需求返工 WorkItem，不允许弱需求静默进入后续阶段。
- Run Manifest `1.2` 会写入 `requirement_evaluations` 和 `summary.requirement_quality_score`。
- `conductor.requirement_benchmark` 和 `python -m app.requirement_benchmark` 支持需求评分、平台产物 vs 模型直出对比。
- 需求阶段已经加入动态评审团队规划，复杂项目可激活多个同职责评审席位，例如 `designer.interaction`、`designer.information_architecture`、`solution_designer.process`、`tester.edge_cases`。
- 动态评审团队规划已结构化持久化到 Collaboration，并写入 Manifest 的 `collaboration_runs[].team_plan`。
- Board Snapshot 已暴露 `design_collaboration.team_plan`，前端可直接展示动态评审团队组成原因。
- Board 模板已渲染 Team Plan 面板，展示复杂度、触发原因、评审席位和 seat focus。
- `python -m app.requirement_benchmark suite` 支持批量 case 对比，用于持续测评平台需求产物是否优于 direct baseline。
- `python -m app.requirement_benchmark run-suite` 支持一键生成平台需求产物、读取或生成 direct baseline、输出 suite 对比报告。
- `python -m app.requirement_benchmark preflight` 支持提前验证 local/cloud LLM backend；`run-suite` 默认会先执行相关 backend 的预检，并支持命令行覆盖 base URL、model、timeout、reasoning effort。
- 评分器已对 `mock` / `mock_fallback` / 占位文档降权，避免 mock 模板被误判为真实需求质量。
- 评分器已扩展到非目标、待确认/假设、边界/异常场景、下游交付约束等维度。
- `run-suite` 生成报告会记录 platform/direct 运行元数据，并在 Markdown 中展开逐项质量检查。
- 需求门禁返工已加入上限，连续返工仍失败会阻塞项目并暴露 blocker，避免无限返工链。
- 需求阶段 LLMHarness、Agent CLI 和协作修订 prompt 已同步要求输出非目标、边界/异常场景、风险与假设、待确认问题、下游交付约束。
- 需求协作加入 Controller 终局裁决：最后一轮存在 `request_changes` 时，只要 lead 最终修订通过需求质量门禁，并覆盖本轮修改意见的核心主题，就可以接受并冻结需求，避免小模型因“已修订但未再投票”陷入返工循环。
- 同一轮只有 peer review 且已按 peer 意见修订时，不再对同一批意见重复修订，降低本地小模型的无效调用成本。

### 3.5 LLMHarness

已实现 OpenAI-compatible LLMHarness：

- 可接本地 LM Studio。
- 可接云端 OpenAI-compatible API。
- 已接入并验证九天 `jiutian-lan-comv3` 云端后端。
- LLM 配置支持 provider preset：`openai`、`jiutian`、`lmstudio`。
- Board LLM 设置页支持本地/云端连接预检。
- 设置接口只返回 `api_key_present`，不会回显真实 API key。
- 保存设置时空 key 会保留本地既有密钥，避免页面编辑误清空。
- Diagnostics 会返回 LLM `health_status`、`recommendation`、模型列表、当前模型是否可见、context length、timeout 和 preflight 状态。
- Diagnostics 输出也会包含当前 project root 下已保存的 `preflight_gate` 快照。
- `python -m app.run_project` 已加入运行前 preflight gate；真实 Agent/LLMHarness 执行前会先检查所选后端，不满足条件时在创建项目之前失败。
- preflight gate 结果会写入 `<project_root>/.conductor/diagnostics/run-preflight/preflight-gate.json`，便于失败后审计和排查。
- Board Snapshot 已暴露 `preflight_gate`，项目列表摘要也暴露 `preflight_gate_status`，前端可展示 gate 状态、文件路径、错误摘要和修复建议。
- Board Snapshot 已暴露 `run_audit`，前端可展示重试次数、失败 WorkItem、范围契约状态和高层风险等级。
- `python -m app.run_project --preflight-only` 可只执行本次 run profile 的 gate 并退出，不创建项目，适合真实运行前做环境验收。
- `--skip-preflight-gate` 可用于受控离线测试或故意跳过环境检查的场景。
- `python -m app.run_project --resume-project-id <project-id>` 可从 `<project_root>/.conductor/state` 读取已有项目并继续执行，不会重新创建 Project。
- `python -m app.run_project --resume-project-id <project-id> --release-stale-tasks` 可在恢复前释放心跳过期的 claimed TaskAssignment，避免长期占用。
- 平台负责写入受控 artifact 文件。
- 对 Qwen thinking 模型默认使用 `reasoning_effort=none`。
- 支持 CLI 参数 `--llm-reasoning-effort none|low|medium|high`。

验证过：

- LM Studio + `qwen/qwen3.6-35b-a3b` 可直连。
- 设计协作流程可以由本地 Qwen3.6 真实产出和评审。
- 九天云端 preflight 返回 `conductor-requirement-preflight-ok`。
- 九天云端 `reading_list` smoke 中，平台需求产物评分 100，direct baseline 评分 58，manifest 记录 `llm_run_count=9`。

### 3.6 Agent CLI 接入

当前支持扫描和绑定以下 Agent CLI：

- `codex`
- `claude`
- `qwen`
- `opencode`
- `aspirecode`
- `aider`
- `gemini`

其中已重点验证：

- Codex CLI：后端代码执行 demo 跑通过。
- OpenCode：之前接入过，后续因订阅用量/环境原因暂停。
- AspireCode：已接入，复用 OpenCode 协议。

AspireCode 接入状态：

- 命令存在：`aspirecode`
- 版本：`0.4.0-20260418-1241`
- 命令协议：`aspirecode run --dir <dir> --model <provider/model> --format default <prompt>`
- 平台新增参数：`--agent-cli aspirecode --aspirecode-model <model>`
- 已真实 smoke：`lmstudio-local/qwen3.6-35b-a3b` 返回 `ok`

AspireCode 可列出的模型：

```text
opencode/big-pickle
opencode/gpt-5-nano
opencode/hy3-preview-free
opencode/minimax-m2.5-free
opencode/nemotron-3-super-free
lmstudio-local/gemma-4-31b-it
lmstudio-local/qwen3.6-35b-a3b
volcengine-plan/ark-code-latest
volcengine-plan/deepseek-v3.2
volcengine-plan/doubao-seed-2.0-code
volcengine-plan/doubao-seed-2.0-lite
volcengine-plan/doubao-seed-2.0-pro
volcengine-plan/doubao-seed-code
volcengine-plan/glm-4.7
volcengine-plan/kimi-k2.5
volcengine-plan/minimax-m2.5
```

AspireCode 注意事项：

- 需要先启动 LM Studio API server：

```powershell
lms server start
```

- AspireCode 默认 prompt 很长，本地模型必须开大 context。已验证需要：

```powershell
lms load qwen/qwen3.6-35b-a3b --identifier qwen3.6-35b-a3b --context-length 32768 -y
```

### 3.7 Harness

已实现：

- `ShellHarness`
- `LLMHarness`
- `StaticWebHarness`

Static Web Harness 能检查：

- `index.html`
- 本地 JS/CSS 资源存在
- JS 语法
- 本地 HTTP 服务
- Playwright 浏览器 smoke
- 基础表单交互
- 导出/下载触发

用于验证静态前端小项目。

### 3.8 失败恢复与重试

已实现基础失败分类和重试策略：

- transient failure
- configuration required
- validation failed
- retryable / non-retryable
- WorkItem retry count
- blocked reason

已补充失败恢复建议，Manifest、报告和 Board Snapshot 可以暴露失败类型、是否可重试、修复建议和 stale claimed task。
仍不是生产级任务调度系统，但已经能避免一些无意义重复执行。

### 3.9 Manifest

已实现 `Run Manifest`，用于记录每次运行：

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
- log path
- report path

当前 schema：`1.28`

Manifest 现在能正确显示：

- Agent 实际后端是 `llm_harness`、`agent_cli`、`harness`、`mock` 等。
- Agent 使用的模型。
- review count / revision count。
- LLM review/revision 的输出文件。
- collaboration phase 与决策统计。
- task assignment claimability、stale 状态、prompt 文件。
- task assignment heartbeat 时间和 heartbeat age。
- task assignment claim token，用于防止旧 prompt 在任务释放重领后误归还。
- artifact lineage：parent、derived_from、review_of、version、collaboration_session_id。
- execution command、exit code、duration，Agent CLI prompt 会脱敏。
- `executions[].prompt_hash`、`cli_runs[].prompt_hash` 与 `llm_runs[].prompt_hash` 记录不可逆 prompt 指纹，支持复现审计和 prompt 变更对比。
- `executions[].token_usage`、`llm_runs[].token_usage` 与 `summary.llm_token_usage` 记录模型实际返回的 token usage，不做估算。
- `summary.llm_context_windows` 与 `llm_runs[].context_length` 记录可用模型上下文窗口，便于判断本地模型是否适合多 Agent 长上下文任务。
- `resume_cursor` 记录当前阶段、下一批待执行 WorkItem、运行中/失败 WorkItem、阻塞原因和建议恢复动作。
- `requirement-generated-suite.json` 记录 `run_config.requirement_review_mode` 和 `platform_runs[].requirement_review_mode`，便于后续真实对比 static review 与 dynamic review。
- runtime environment 和 platform diagnostics。
- `llm_runtime_config` 只记录 key 是否存在，不记录真实 API key。
- `run_environment.command_argv` 会脱敏 `key`、`token`、`secret`、`password` 相关参数。
- 如果存在运行前 preflight gate 文件，Manifest 会索引 `files.preflight_gate`，并汇总 `summary.preflight_gate_ok`、`summary.preflight_gate_errors` 与 `summary.preflight_gate_recommendations`。
- `retry_history` 会结构化记录发生过重试、失败或阻塞的 WorkItem，包括 retry count、max retries、是否耗尽、failure type、相关事件和阶段 gate history。
- `scope_contract_results` 会审计下游 artifact 是否违反冻结需求里的硬性非目标/范围排除，并汇总 `summary.scope_contract_status` 与 `summary.scope_contract_violation_count`。
- summary 聚合：状态计数、失败 WorkItem、可重试/不可重试失败数、CLI/LLM/collaboration 运行数、变更文件数、artifact 文件数、验证失败数。
- 项目 Markdown Report 会展示 `## Preflight Gate` 和 `## Scope Contract Audit` 小节，包含 gate 文件路径、修复建议和冻结需求范围审计结果。

示例真实项目：

```text
C:\99_self\conductor_test\qwen36_design_peer_review_20260506_02
```

该项目 manifest 显示：

- 6 个 Agent 都使用 `llm_harness`
- 模型为 `qwen/qwen3.6-35b-a3b`
- 记录了 16 条 LLM 运行
- 包含设计初稿、review、revision 与 collaboration 统计

### 3.10 Windows 中文问题

已做部分处理：

- CLI 入口配置 UTF-8 stdio。
- 建议中文需求通过 UTF-8 文件输入，不建议直接 PowerShell inline。
- 不应仅凭 PowerShell `Get-Content` 乱码判断文件损坏，Python `read_text(encoding="utf-8")` 更可靠。

## 4. 运行方式

### 4.1 运行测试

```powershell
cd C:\99_self\conductor\conductor-ai
python -m pytest -q
```

最近一次验证结果：

```text
376 passed
```

### 4.2 运行 Board

```powershell
cd C:\99_self\conductor\conductor-ai
python -m app.board
```

默认后端端口曾使用 `8765`。若前端分离运行，需要注意 CORS 和缓存旧页面问题。

### 4.3 使用 LLMHarness 运行设计阶段

```powershell
lms server start
lms load qwen/qwen3.6-35b-a3b --identifier qwen/qwen3.6-35b-a3b --context-length 8192 -y

python -m app.run_project `
  --requirement-file C:\path\to\requirement.txt `
  --project-root C:\99_self\conductor_test `
  --project-name qwen_design_case `
  --run-profile design_cli_only `
  --llm-harness local `
  --llm-base-url http://127.0.0.1:1234/v1 `
  --llm-model qwen/qwen3.6-35b-a3b `
  --llm-timeout 900 `
  --llm-reasoning-effort none
```

### 4.4 使用 AspireCode

```powershell
lms server start
lms load qwen/qwen3.6-35b-a3b --identifier qwen3.6-35b-a3b --context-length 32768 -y

python -m app.run_project `
  --requirement-file C:\path\to\requirement.txt `
  --project-root C:\99_self\conductor_test `
  --project-name aspirecode_case `
  --run-profile design_cli_only `
  --agent-cli aspirecode `
  --aspirecode-model lmstudio-local/qwen3.6-35b-a3b
```

## 5. 已验证案例

### 5.1 Qwen3.6 多 Agent 设计协作

项目目录：

```text
C:\99_self\conductor_test\qwen36_design_peer_review_20260506_02
```

需求：

```text
个人读书清单 Web 应用：添加书名、作者、阅读状态、评分、备注；按状态筛选；导出 CSV；刷新后保留数据。
```

验证结果：

- designer 真实产出设计文档。
- requirement_designer / solution_designer 参与同侪评审。
- backend / frontend / tester 参与跨职能评审。
- 评审循环能生成 review 和 revision artifact。
- 最终状态为 `in_progress`，原因是达到最大评审轮数后仍有 reviewer 要求修改。
- 这不是模型调用失败，而是质量门控没有放行。

### 5.2 AspireCode smoke

测试目录：

```text
C:\99_self\conductor_test\aspirecode_probe_20260506_01
```

命令：

```powershell
aspirecode run --dir C:\99_self\conductor_test\aspirecode_probe_20260506_01 --model lmstudio-local/qwen3.6-35b-a3b --format default "Return exactly: ok"
```

验证结果：

```text
ok
```

前置条件：

- `lms server start`
- `qwen3.6-35b-a3b` 以 32768 context 加载

### 5.3 Qwen2.5 需求阶段真实对比

本次验证使用 LM Studio 本地模型：

```text
qwen2.5-coder-14b-instruct
base_url: http://127.0.0.1:1234/v1
context: 32768
```

受控命令：

```powershell
python -m app.requirement_benchmark run-suite `
  --output-dir C:\tmp\conductor_requirement_real_qwen25_suite_plain_arbitrated `
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

结果：

- `reading_list`: platform 100, direct 42, delta 58。
- `expense_approval`: platform 87, direct 39, delta 48。
- `csv_cleaner`: platform 88, direct 44, delta 44。
- Summary: 3/3 passed, platform wins 3, average platform score 91.67, average direct score 41.67, average delta 50.0。
- 运行报告：`C:\tmp\conductor_requirement_real_qwen25_suite_plain_arbitrated\requirement-comparison-results.md`
- JSON：`C:\tmp\conductor_requirement_real_qwen25_suite_plain_arbitrated\requirement-comparison-results.json`

验证结论：

- 需求阶段平台链路在本地小模型上能稳定优于不接平台的 plain direct baseline。
- 终局裁决修复了此前“最终修订已合格但 max_rounds_reached 触发返工”的问题。
- 3 个 platform run 都在需求冻结后进入 `design`，说明需求阶段 gate 已放行。

## 6. 当前不足

### 6.1 全链路生产级还未完成

平台已经能做真实需求协作、设计协作和部分代码执行验证，但还没有稳定达到“任意需求从需求到代码到测试全自动成功”的生产级状态。

主要差距：

- 需求阶段已有真实 benchmark 通过，但评分器的关键词覆盖仍偏硬，`expense_approval` / `csv_cleaner` 出现低 keyword coverage 提示，需要继续校准。
- 需求设计质量仍依赖模型能力、prompt 和本地模型稳定性。
- 开发阶段真实代码执行仍需更强的任务边界和验收闭环。
- 测试阶段需要更明确区分生成项目测试和平台自身测试。

### 6.2 Board 暂缓

前端 Board 已多轮尝试，但当前用户倾向先暂停前端视觉推进，优先打磨后端能力和日志可观测性。

### 6.3 AspireCode 需要长上下文

AspireCode 内置 agent prompt 很长，本地模型 4096 context 会失败。使用本地模型时必须显式加载大 context。

### 6.4 Manifest 仍可继续加强

Manifest 已能记录主要运行事实和审计摘要，并已完成 LLM 配置、命令行密钥脱敏、`resume_cursor`、基础 token usage、可配置成本估算和只读 replay trace。后续可以继续补：

- side-effect replay executor

已补一个只读 manifest verifier：
- `python -m app.verify_manifest <manifest>`
- 检查 schema 基础字段、summary 计数、`resume_cursor`、WorkItem/Execution/Artifact 链接和引用文件。
- 当前它不重跑 Agent，也不恢复状态；定位是 replay/resume 前的归档自检层。
- `python -m app.run_project` 的最终 JSON payload 已包含 `manifest_verification`，每次运行后立即暴露归档自检结果。
- `python -m app.run_project --write-replay-trace` 可在项目运行结束时同步归档只读 replay trace。
- `python -m app.verify_manifest <manifest> --output <file>` 可将自检报告归档到 JSON 文件。
- 针对测试：`python -m pytest tests\test_replay_verifier.py -q`，结果 `7 passed`。
- 已补只读 replay trace：`python -m app.replay_manifest <manifest> --format markdown`，从 manifest 还原 Project/WorkItem/TaskAssignment/Execution/Artifact 时间线，不重跑 Agent；支持 `--output` 归档到文件，且不会输出 claim token。
- 针对测试：`python -m pytest tests\test_replay_trace.py tests\test_replay_verifier.py -q`，结果 `12 passed`。
- Task Center context 已显式暴露 `frozen_requirement_baseline`，Markdown prompt 会单独强调冻结需求是下游设计、开发、测试的控制性合同。
- 需求评分器已补中文语义 alias 和 `metrics.keyword_matches`，benchmark 报告可看到每个关键词实际命中的表达，便于定位 keyword coverage 误报。
- 需求评分器已补范围扩张检测，能标记原始需求未要求但文档新增的登录、支付、通知、后台报表等功能；明确写在非目标里的排除项不会被误判。
- Requirement benchmark Markdown 报告已展示 keyword coverage、缺失关键词、命中 alias 和 scope expansion，方便直接从报告定位评分问题。
- 当前全量测试：`python -m pytest -q`，结果 `389 passed`。

### 6.5 任务中心仍是轻量实现

目前已有 TaskAssignment、上下文渲染、prompt 归档、heartbeat、stale release、resume 前 stale cleanup、文件锁和损坏 state 隔离，但还不是独立分布式队列。
CLI worker mutation 已强制要求 `--agent-id` 和 `--claim-token`，避免外部 worker 误归还或篡改其他 worker 的任务；`release-stale` 保持为 operator cleanup 路径。

## 7. 建议下一步

优先级建议：

1. 校准需求评分器：改进中文关键词抽取和领域实体覆盖，减少“章节完整但 keyword coverage 偏低”的误报。
2. 跑动态评审真实 benchmark，对比 direct plain、static review、dynamic review 的质量和成本。
3. 强化 frozen requirement 到 design/development/testing 的上下文传递，保证后续阶段以冻结需求为基线。
4. 做一个小型真实项目闭环：静态 Web 项目优先，从需求冻结、设计、代码生成、StaticWebHarness 验证到 manifest/report 归档。
5. 继续产品化 Task Center：增加更完整的 lease 策略、并发压力测试、长期 worker 审计和分布式队列边界。
6. 开始把开发/测试阶段也做成类似需求阶段的产品级闭环。
7. 继续强化运行前 gate：对失败类型做更细分的降级策略，并把 gate 结果接入更明确的运行前操作提示。

## 8. 当前关键命令备忘

```powershell
# 测试
python -m pytest -q

# LM Studio server
lms server start
lms server status

# 加载 Qwen 给 LLMHarness
lms load qwen/qwen3.6-35b-a3b --identifier qwen/qwen3.6-35b-a3b --context-length 8192 -y

# 加载 Qwen 给 AspireCode
lms load qwen/qwen3.6-35b-a3b --identifier qwen3.6-35b-a3b --context-length 32768 -y

# 查看模型
lms ps

# AspireCode 模型列表
aspirecode models

# AspireCode smoke
aspirecode run --dir C:\99_self\conductor_test\aspirecode_probe_20260506_01 --model lmstudio-local/qwen3.6-35b-a3b --format default "Return exactly: ok"
```
