# Conductor AI

Conductor AI 是一个面向软件项目全生命周期的 AI 项目执行与多智能体协作平台。它关注的不是一次性生成代码片段，而是把一个项目从需求理解、方案设计、任务拆解、协作执行、质量门禁、人工审批、交付验证推进到可审计、可回放的交付结果。

平台的长期目标是让 AI 以结构化团队的方式参与软件项目：不同职责的 Agent 围绕同一份项目状态工作，控制层负责流程推进和质量判断，执行层负责具体任务产出，所有关键结果都通过产物、测试、接口、状态和审计记录来证明。

长期产品定位详见 [docs/platform_positioning.md](docs/platform_positioning.md)。当前实现状态详见 [CURRENT_STATE.md](CURRENT_STATE.md)。

## 核心设计逻辑

Conductor 的核心不是“多叫几个 Agent”，而是把多智能体协作放进可控的项目执行系统中。

- **项目驱动**：平台围绕项目目标、阶段、依赖、任务和交付状态运行，而不是围绕单次对话或孤立任务运行。
- **控制层与执行层分离**：Controller 负责阶段推进、门禁判断、返工决策和最终状态；Planner、Runner、专业 Agent 和外部工具负责具体执行。
- **共享状态与任务中心**：项目状态、WorkItem、TaskAssignment、产物和人工控制动作集中记录，外部 Agent 通过 Task Center 领取、返回、释放和审计任务。
- **证据优先**：Agent 声称完成不等于完成。平台优先依赖冻结需求、设计产物、API 响应、测试结果、持久化证据、manifest、report 和 replay verifier。
- **可观测与可接管**：平台提供 JSON API 和 Web 控制台，展示依赖图、任务、智能体、产物、日志、设置和人工控制状态；人可以暂停、审批、拒绝、覆盖或恢复流程。
- **可恢复与可回放**：关键状态、事件、产物和执行记录被保留下来，用于审计、复盘、问题定位和后续恢复。

## 使用方式

常用验证命令：

```powershell
python -m compileall -q app conductor tests
python -m pytest tests/test_execution_config.py tests/test_system_config.py tests/test_harness.py tests/test_scope_contract.py tests/test_board_api.py -q
python -m app.run_project --project-root C:\99_self\conductor_test\api-demo --requirement "Build a todo items backend REST API with create, list, update, delete, and stats endpoints." --run-profile api_mock
```

演示模式：

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\demo-check.ps1 -StaticSmoke
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\demo-start.ps1 -Open
```

打开 `http://127.0.0.1:4176/?demo=1`。演示模式会在本地加载完整的纯 API 交付故事，包括依赖图、任务分配、智能体、产物、日志、演示步骤和就绪度推进。如果实时 API 未运行并且控制台出现连接错误，点击 `加载演示` 进入离线展示路径，或点击 `设置` 检查后端 URL。在实时模式中，运行项目操作前请使用 `检查后端` 确认已配置的 API URL 可响应。演示讲解流程见 `frontend/DEMO_SCRIPT.md`。

更完整的演示回归子集：

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\demo-check.ps1 -StaticSmoke -FullBackendChecks
```

启动和停止平台控制台：

```text
start.bat
stop.bat
```
