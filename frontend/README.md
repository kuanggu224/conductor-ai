# Conductor 前端

Conductor API 平台的 C3 依赖风险控制界面。

本地运行：

从仓库根目录双击：

```text
start.bat
stop.bat
```

运行离线演示：

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\demo-start.ps1 -Open
```

`demo-start.ps1` 会先运行前端预检，包括静态资源冒烟检查，再启动演示服务。如果临时冒烟端口被占用，可传入 `-PreflightSmokePort 4179`。

打开：

```text
http://127.0.0.1:4176/?demo=1
```

前端默认使用 `http://127.0.0.1:8000`。可通过下面的 URL 覆盖：

```text
http://127.0.0.1:4176/?api=http://127.0.0.1:8000
```

离线演示模式：

```text
http://127.0.0.1:4176/?demo=1
```

演示模式会在本地加载完整的纯 API 交付项目，因此即使后端未运行，依赖图、任务中心、智能体、评审产物和日志也会保持可见。使用顶部的 `演示` / `实时` 切换模式。`设置` 页面包含能力对齐面板，用来说明确定性演示行为对应的实时 API 或运行时依赖。在实时模式中，运行项目操作前请使用 `检查后端` 确认已配置的 API URL 可响应。

逐步演示流程见 `frontend/DEMO_SCRIPT.md`。

预检：

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\demo-check.ps1 -StaticSmoke
```

覆盖范围：

- `依赖图`：项目健康度、任务依赖、产物、智能体节点、人工门禁、操作控制台。
- `任务`：列表、上下文、领取、领取下一项、批量领取、完成、失败、心跳、释放、释放过期/已过期、清理。
- `智能体`：花名册、动态激活、可领取任务列表、智能体侧领取。
- `评审`：产物详情、暂停/恢复、请求审批、批准/拒绝/覆盖。
- `日志`：最近事件、路由、执行记录、运行时流刷新。
- `设置`：实时模式下的 API 配置和后端状态检查；演示模式下的离线运行时、诊断、LLM 预检证据和演示/实时能力对齐。
- `Todo`：列表、创建、读取、更新、删除。
