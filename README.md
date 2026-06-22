# Conductor AI

Conductor AI is a backend/API orchestration platform for planning, executing, validating, and auditing software delivery work.

Current scope is backend/API-only:

- Requirement clarification and frozen requirement artifacts.
- Backend/API design and feature-slice planning.
- Backend/API implementation workitems.
- Shell, LLM, Agent CLI, API mock, and API SQLite execution paths.
- API validation, delivery readiness, manifests, reports, replay verification, and Task Center handoff.
- JSON API control surface for project state, tasks, artifacts, settings, and human-control operations.

Removed scope:

- Server-rendered product views.
- Static asset serving for product delivery.
- Static web and combined browser/API delivery profiles.
- Client implementation roles and workitem kinds.

Useful commands:

```powershell
python -m compileall -q app conductor tests
python -m pytest tests/test_execution_config.py tests/test_system_config.py tests/test_harness.py tests/test_scope_contract.py tests/test_board_api.py -q
python -m app.run_project --project-root C:\99_self\conductor_test\api-demo --requirement "Build a todo items backend REST API with create, list, update, delete, and stats endpoints." --run-profile api_mock
```

Demo:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\demo-check.ps1 -StaticSmoke
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\demo-start.ps1 -Open
```

打开 `http://127.0.0.1:4176/?demo=1`。演示模式会在本地加载完整的纯 API 交付故事，包括依赖图、任务分配、智能体、产物、日志、演示步骤和就绪度推进。如果实时 API 未运行并且控制台出现连接错误，点击 `加载演示` 进入离线展示路径，或点击 `设置` 检查后端 URL。在实时模式中，运行项目操作前请使用 `检查后端` 确认已配置的 API URL 可响应。演示讲解流程见 `frontend/DEMO_SCRIPT.md`。

For the broader demo regression subset:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\demo-check.ps1 -StaticSmoke -FullBackendChecks
```

Live API console:

Double-click from the repository root:

```text
start.bat
stop.bat
```
