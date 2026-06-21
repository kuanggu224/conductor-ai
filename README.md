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
