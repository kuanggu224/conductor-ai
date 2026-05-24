# Conductor

Conductor 是一个 AI Native 的项目执行系统：它把 AI 组织成一个可控的软件项目团队，由 Controller 管流程，由多 Agent 承担职责，由 Shared State 记录事实，由 Manifest 审计交付过程。

项目的目标不是做一个更长上下文的聊天工具，而是把自然语言需求推进成可冻结、可追踪、可验证、可恢复的项目交付链路。完整设计说明见 [docs/conductor_platform_guide.md](docs/conductor_platform_guide.md)，当前稳定状态见 [CURRENT_STATE.md](CURRENT_STATE.md)。

## 核心理念

- `Project First`：平台以项目为核心对象，而不是以一次对话为核心。
- `Controller Owns Flow`：Agent 不直接决定项目是否推进，流程由 Controller 和状态机控制。
- `Shared State Is Source of Truth`：需求、设计、任务、产物、失败、事件和 Agent 行为都落入共享状态。
- `Artifacts Over Chat`：交付物必须沉淀成 Artifact，并能被下游阶段引用。
- `Auditability By Default`：Manifest、Replay、Report、Event Log 用来证明系统做过什么、为什么这么做、结果是否可信。
- `Human In Control`：AI 可以自主执行，但人类必须能暂停、接管、审批和恢复。

## 当前能力

- 需求阶段：支持需求评审、修订、质量门禁、冻结需求规格和需求返工。
- 设计阶段：基于冻结需求生成设计产物，冻结设计会进入开发、测试和 Task Center 上下文。
- 开发阶段：支持 mock、ShellHarness、StaticWebHarness、LLMHarness、CLI Agent 等执行路径。
- 测试阶段：支持命令测试、静态 Web smoke、需求覆盖检查、testing checklist 证据契约、API 行为证据和失败回流返工，返工反馈会携带验证命令和退出码证据。
- Task Center：支持外部 Agent 领取、续租、归还、失败、释放、批量领取、stale sweep 和上下文 prompt 生成。
- TL Agent：支持动态团队规划，并按运行失败、历史风险、开发范围复杂度、返工证据、测试证据契约和并行集成风险扩缩团队。
- Human Control：提供 CLI/API 控制路径，支持暂停、恢复、接管、TL 驱动的升级审批、多项目 hold 汇总和项目报告审计。
- Manifest/Replay：记录运行事实，支持 manifest 校验、审计 bundle 和只读 replay trace。

## 推荐运行方式

默认使用本地确定性路径，不需要真实 LLM 或外部 CLI Agent：

```powershell
python -m pytest -q
python -m app.run_project --project-root C:\99_self\conductor_test\api-demo --requirement "Build a backend REST API for todo items" --run-profile api_mock
python -m app.run_project --project-root C:\99_self\conductor_test\sqlite-demo --requirement "Build a backend REST API for todo items with SQLite database persistence" --run-profile api_sqlite
python -m app.run_project --project-root C:\99_self\conductor_test\fullstack-demo --requirement "Build a fullstack web app for todo items with a browser frontend and backend REST API" --run-profile fullstack_web
python -m app.run_project --project-root C:\99_self\conductor_test\demo --requirement "Build a small static web app"
```

继续已有项目：

```powershell
python -m app.run_project --project-root C:\99_self\conductor_test\demo --resume-project-id <project-id>
```

只做运行前检查：

```powershell
python -m app.run_project --project-root C:\99_self\conductor_test\demo --requirement "Build a small static web app" --preflight-only
python -m app.diagnostics
python -m app.diagnostics --probe-cli
python -m app.diagnostics --preflight-llm
```

`--probe-cli` 会在 CLI 诊断里输出 `auth_status`、`auth_error` 和 `recommendation`。如果 selected CLI 出现未登录、认证过期、API key 缺失等常见授权失败，preflight gate 会阻断真实运行并给出 login/auth 恢复建议。

`--preflight-llm` 会在 LLM 诊断里输出 `failure_category`，用于区分 auth、quota、context length、model not found、timeout、network、server error 等常见 provider 故障，并给出更具体的恢复建议。

测试项目建议放在 `C:\99_self\conductor_test\...` 下，避免把临时产物写进仓库或 `C:\` 根目录。

## Task Center

Task Center 是外部 Agent 或人类 worker 接入 Conductor 的轻量任务中心。它不是完整分布式队列，但已经提供稳定的文件状态边界、claim token、lease、heartbeat、stale release 和任务归还协议。

常用命令：

```powershell
python -m app.task_center list --project-root <project-root>
python -m app.task_center summary --project-root <project-root>
python -m app.task_center claim-next --project-root <project-root> --agent-id <agent-id> --with-context
python -m app.task_center context <assignment-id> --project-root <project-root> --format markdown
python -m app.task_center complete <assignment-id> --project-root <project-root> --result-summary "done"
python -m app.task_center fail <assignment-id> --project-root <project-root> --blocked-reason "reason"
python -m app.task_center release <assignment-id> --project-root <project-root> --release-reason "worker interrupted"
python -m app.task_center release-stale --project-root <project-root> --stale-after-seconds 3600
python -m app.task_center maintenance --project-root <project-root> --stale-after-seconds 3600
python -m app.task_center watchdog --project-root <project-root> --interval-seconds 60
python -m app.run_project --project-root <project-root> --resume-project-id <project-id> --resume-plan-only
```

Task Center context 会暴露冻结需求、冻结设计、delivery contract、testing checklist、rework feedback、pending retest scope、handoff safety，以及动态 Agent/write scope 风险。返工任务会在 `rework_context.testing_feedback` 中携带失败测试的验证命令/退出码，并在 `rework_context.pending_retest_scope` 中给出修复后应优先复跑的最小测试范围。`handoff_safety.baseline_handoff` 会标记开发任务是否已经把需求/设计基线写入 acceptance criteria，方便外部 Agent 在明确边界内工作。

`tasks-for-agent` 和 Board API 的动态 Agent task list 会为 claimable 任务输出精确的 `claim_command`、`claim_with_context_command` 和 API claim path；当任务因依赖或 write-scope 冲突不可领取时命令为空，外部 CLI Agent 可据此避免误 claim。

任务被 claim 后，CLI、Board API 和 context JSON 的 task payload 会返回 `return_commands`，包含带当前 `agent_id` 和 `claim_token` 的 `complete`、`complete_with_output_file`、`fail`、`fail_with_output_file`、`heartbeat`、`release` 命令，方便外部 worker 按同一协议归还任务和 Artifact。Board API task payload 还会提供 `return_api_paths`，供 API worker 直接调用。

CLI/API 在创建外部输出 Artifact 前会先校验 claim 状态、`agent_id` 和 `claim_token`，错误 worker 或 stale token 的归还请求不会留下未引用的外部 Artifact；Task Center audit/maintenance 也会报告历史遗留的 `orphan_external_artifact`，并通过 `related_artifact_ids` 保留可校验引用。缺失输入/输出 Artifact 会进入 `missing_artifact_ids`，便于调度器或 operator 精确修复状态；Project Report 和 Replay trace 也会渲染这些关联/缺失 Artifact id，避免修复线索只存在于原始 JSON。

`run_project --maintenance-task-center` 可在恢复项目前执行 Task Center 维护；输出的 report/latest 会包含 `attention_project_ids`、`finding_code_counts`、`recommendations`、pending retest scope、active human-control holds、operator guidance，以及可复制的维护、状态检查和 human-control hold 检查命令，便于长周期项目接入调度器。

## Manifest、Replay 和审计

校验单个 Manifest：

```powershell
python -m app.verify_manifest <project-root>\.conductor\manifests\<project-id>.manifest.json --fail-on-warnings
```

生成 replay trace：

```powershell
python -m app.replay_manifest <project-root>\.conductor\manifests\<project-id>.manifest.json --format markdown
```

项目运行时写入审计 bundle：

```powershell
python -m app.run_project --project-root <project-root> --requirement-file requirement.txt --write-audit-bundle
python -m app.verify_audit_bundle <project-root>\.conductor\replay\<project-id>.audit.json
```

Manifest verifier 会检查 schema、Project/WorkItem/Execution/Artifact/TaskAssignment 链接、delivery contract、acceptance trace、testing checklist、敏感凭据泄露、引用文件和 replay/audit 一致性。
Replay trace 会渲染返工 lineage、testing feedback、pending retest scope 和 Human Control actions，便于只读复盘项目为何暂停、审批、恢复或 override。
Audit bundle schema `1.2` 会把 Manifest schema、最终状态、`summary.pending_test_scope`、human control action count 和 active human hold 写入 bundle summary，并在 `verify_audit_bundle` 中与 Manifest 内容交叉校验，确保返工后的复验范围和人工接管状态进入最终审计包。

## 真实 LLM 配置

真实 LLM 配置保存在 `.conductor/llm.config.json`，该文件被 Git 忽略，不要提交 API key。

Jiutian 或 OpenAI-compatible 后端可通过 Board 设置页或本地配置启用。验证前建议先运行：

```powershell
python -m app.requirement_benchmark preflight --backend cloud --output-dir .conductor\diagnostics\cloud-preflight
```

运行真实模型验证不建议每次小改都跑完整链路，应在一批后端能力稳定后集中验证。

## 项目结构

- `app/`：命令入口、Board API、诊断和工具命令。
- `conductor/domain/`：Project、WorkItem、Artifact、Agent、Execution、SharedProjectState 等核心模型。
- `conductor/controller/`：流程推进、阶段转换、TL Agent 集成和运行决策。
- `conductor/task_center/`：任务领取、上下文生成、维护和外部 worker 协议。
- `conductor/execution/`：Runner、Harness、CLI/LLM 执行路径。
- `conductor/reports/`、`conductor/replay_*`：报告、Manifest、Replay 和审计校验。
- `docs/`：平台设计、使用手册和协议文档。
- `tests/`：单元测试和回归测试。

## 当前开发重点

根据平台指南，下一阶段优先级是：

1. 扩展真实端到端案例，覆盖表单流、文件处理流、API mock 流和失败返工流。
2. 强化 Development 和 Testing 阶段，让它们接近 Requirement 阶段的闭环质量。
3. 产品化 Task Center 长周期运行能力，包括维护报告、定时 sweep、恢复策略和守护进程。
4. 增强动态 Agent 规划，让 TL Agent 根据风险、复杂度和历史表现动态扩缩团队。
5. 在后端日志、Manifest 和 Task Center 稳定后，再继续推进 Web Board。

## Windows UTF-8

如果 PowerShell 显示中文乱码，先启用 UTF-8：

```powershell
. .\scripts\windows-utf8.ps1
```

Conductor 的 Python 入口会配置 UTF-8 stdio；这个脚本主要用于 `Get-Content`、`type` 和终端日志查看。
