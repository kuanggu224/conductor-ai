# Conductor 平台进度总结

更新时间：2026-05-07

本文档用于把当前 Codex 桌面端对话中的关键上下文沉淀到仓库，方便后续通过 Codex CLI、其他 Agent CLI 或人工继续开发。

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
- 支持 `design -> development -> testing` 主流程。
- 支持 `SharedProjectState` 内存状态与 `FileStateStore` 文件持久化。
- 运行项目时可以指定项目根目录。
- 测试项目统一约定放在 `C:\99_self\conductor_test\<case_name>`。

### 3.2 Task Center 雏形

- WorkItem 可以被 Agent 领取、完成、失败或阻塞。
- 状态里已有 `TaskAssignment`。
- 目前还是轻量任务中心，不是完整分布式队列。

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

### 3.5 LLMHarness

已实现 OpenAI-compatible LLMHarness：

- 可接本地 LM Studio。
- 可接云端 OpenAI-compatible API。
- 平台负责写入受控 artifact 文件。
- 对 Qwen thinking 模型默认使用 `reasoning_effort=none`。
- 支持 CLI 参数 `--llm-reasoning-effort none|low|medium|high`。

验证过：

- LM Studio + `qwen/qwen3.6-35b-a3b` 可直连。
- 设计协作流程可以由本地 Qwen3.6 真实产出和评审。

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
- log path
- report path

当前 schema：`1.1`

Manifest 现在能正确显示：

- Agent 实际后端是 `llm_harness`、`agent_cli`、`harness`、`mock` 等。
- Agent 使用的模型。
- review count / revision count。
- LLM review/revision 的输出文件。
- collaboration phase 与决策统计。

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
182 passed
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

## 6. 当前不足

### 6.1 全链路生产级还未完成

平台已经能做真实设计协作和部分代码执行验证，但还没有稳定达到“任意需求从需求到代码到测试全自动成功”的生产级状态。

主要差距：

- 需求设计质量还依赖模型能力和 prompt。
- 开发阶段真实代码执行仍需更强的任务边界和验收闭环。
- 测试阶段需要更明确区分生成项目测试和平台自身测试。

### 6.2 Board 暂缓

前端 Board 已多轮尝试，但当前用户倾向先暂停前端视觉推进，优先打磨后端能力和日志可观测性。

### 6.3 AspireCode 需要长上下文

AspireCode 内置 agent prompt 很长，本地模型 4096 context 会失败。使用本地模型时必须显式加载大 context。

### 6.4 Manifest 仍可继续加强

Manifest 已能记录主要运行事实，但后续可以继续补：

- token usage
- cost
- prompt hash
- exact command
- environment snapshot
- model context length
- LM Studio server status

### 6.5 任务中心仍是轻量实现

目前已有 TaskAssignment，但还不是独立队列或可并发多 worker 领取任务的系统。

## 7. 建议下一步

优先级建议：

1. 强化任务中心：定义 WorkItem claim/return 协议，支持 Agent 从任务中心领取任务并归还结构化结果。
2. 做 Agent CLI 执行健康检查：每个 CLI 支持 probe，记录可用模型、连通性、context 风险。
3. 完善 AspireCode 绑定：支持不同 Conductor role 绑定 AspireCode 内部 agent，例如 `需求分析Agent`、`编码Agentic`、`测试Agent`。
4. 做一个小型真实项目闭环：静态 Web 项目优先，从需求设计、代码生成、静态验证到 manifest 归档。
5. 强化 ContextBuilder：按 artifact lineage 选择上下文，减少无效长 prompt。
6. 继续改进失败恢复：失败后自动建议“换模型 / 增加 context / 降低 prompt / 切换 CLI”。

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

