# Current State

The platform has been cleaned to backend/API-only scope.

Verified state:

- Runtime code and tests no longer contain the removed client role or workitem kinds.
- Static web and combined browser/API delivery harnesses have been removed.
- Server-rendered product view files and static asset delivery directories are absent.
- API mock and API SQLite profiles remain available.
- Board-related server code now exposes JSON state and control APIs only.
- Task Center, manifest, replay verifier, delivery readiness, and requirement coverage now use backend/API validation semantics.

Validation performed:

```powershell
python -m compileall -q app conductor tests
python -c "import app.board; import app.human_control; from conductor.controller.engine import ConductorEngine; from conductor.execution.planner import Planner; from conductor.execution.runner import Runner; print('runtime imports ok')"
python -m pytest tests/test_execution_config.py tests/test_system_config.py tests/test_harness.py tests/test_scope_contract.py tests/test_board_api.py -q
```

Known notes:

- Historical docs were rewritten to avoid describing removed client delivery paths.
- The active execution model is backend/API requirement, design, implementation, validation, and audit.
