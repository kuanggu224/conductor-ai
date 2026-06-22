# Conductor 演示脚本

当外部 Agent CLI 或 LLM 绑定不稳定时，使用该脚本进行可控平台演示。

## 启动

```powershell
cd C:\99_self\conductor\conductor-ai
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\demo-start.ps1 -Open
```

打开：

```text
http://127.0.0.1:4176/?demo=1
```

`demo-start.ps1` 会先运行相同的前端预检，包括静态资源冒烟检查，然后再启动页面服务。

首屏预期：

- 项目选择器显示 `演示 / 构建纯 API Todo 服务...`。
- 顶部状态为 `进行中`。
- 风险面板显示 `中`、`有风险 / 78`、`可领取 1`、`阻塞 1`。
- 依赖图展示需求、设计、开发、测试、交付、任务分配、智能体和产物节点。

## 能力边界

- 演示模式是确定性离线模式：不需要后端、Agent CLI 或外部 LLM 服务商。
- 演示模式会在本地覆盖操作控制台、依赖图、任务交接、智能体、产物、设置诊断、日志和 Todo CRUD。
- `设置` 页面展示 `能力对齐`：每个演示区域都对应实时 API 或运行时依赖。
- 在实时模式中，`检查后端` 会调用 `/api/status`，用于在项目操作前确认 API 在线。
- 实时模式只应在后端运行于已配置 API URL 时使用。
- 更完整的后端演示回归子集由 `demo-check.ps1 -StaticSmoke -FullBackendChecks` 覆盖。

## 讲解流程

1. 依赖图
   - 说明依赖图是平台的操作员视图：阶段、WorkItem、任务分配、智能体、产物和人工门禁。
   - 指向 `风险控制`，展示就绪度、阻塞项和可领取工作。
   - 打开 `实时状态`，展示当前项目载荷。

2. 任务中心
   - 点击 `任务`。
   - 为 `assignment-api-validation` 打开 `上下文`。
   - 说明上下文会把冻结需求、必需输入和预期输出打包给智能体。
   - 点击 `领取下一项`，模拟智能体领取下一个可领取任务。

3. 进度推进
   - 返回 `依赖图`。
   - 点击 `推进`。
   - 预期信号：就绪度变为 `有风险 / 92`，风险变为 `低`，阻塞项变为 `0`。
   - 点击 `运行`。
   - 预期信号：项目状态变为 `就绪`，就绪度变为 `就绪 / 96`，`批准` 变为可用。

4. 评审
   - 点击 `评审`。
   - 打开 `FastAPI SQLite 实现`。
   - 说明该产物记录了变更文件、pytest 契约验证和 SQLite 证据。
   - 打开 `API 契约验证`，展示端点证据和交付就绪度。

5. 实时模式
   - 点击 `设置`，指向 `能力对齐`，区分确定性演示行为和实时后端/Agent/LLM 依赖。
   - 如果要切换到实时模式，先点击 `检查后端`；运行 `推进` 或 `运行` 前预期状态为 `在线`。
   - 只有当后端运行在已配置 API URL 时，才点击顶部的 `实时`。
   - 如果后端没有运行，展示时保持演示模式。

## 兜底

- 如果项目列表为空，使用 `?demo=1`。
- 如果出现实时 API 错误横幅，点击 `加载演示` 进入离线展示路径，或点击 `设置` 检查后端 URL。
- 如果某个操作返回后端错误，点击 `演示` 重新加载本地夹具。
- 如果端口 `4176` 被占用，换端口启动：`.\scripts\demo-start.ps1 -Port 4177`。
- 如果预检端口 `4178` 被占用，换预检端口启动：`.\scripts\demo-start.ps1 -PreflightSmokePort 4179`。
- 如果依赖图看起来过宽，刷新页面；响应式布局已按桌面和 390px 移动宽度验证。

## 验证

演示前运行：

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\demo-check.ps1 -StaticSmoke
```

更完整的 API 交付回归子集：

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\demo-check.ps1 -StaticSmoke -FullBackendChecks
```
