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

Open `http://127.0.0.1:4176/?demo=1`. Demo mode loads a complete API-only delivery story locally, including dependency graph, task assignments, agents, artifacts, logs, runbook steps, and readiness progression. If the live API is not running and the console shows a connection error, click `Load Demo`. Use `frontend/DEMO_SCRIPT.md` for the presentation talk track.

For the broader demo regression subset:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\demo-check.ps1 -StaticSmoke -FullBackendChecks
```

Live API console:

```powershell
python -m uvicorn app.board:app --host 127.0.0.1 --port 8000
python -m http.server 4176 -d frontend
```
