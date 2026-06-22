# Current State

This document is a current implementation snapshot. It is not the long-term product boundary.

## Product Boundary

Conductor's long-term positioning is an AI project execution and multi-agent collaboration platform for the full software project lifecycle.

The current codebase is focused on the core control layer, backend/API execution chain, Task Center handoff, verification/audit flows, and the platform's own management console. Frontend or full-stack project delivery is currently paused or absent in the implementation, not permanently removed from the product vision.

## Verified Current Implementation

- Runtime code and tests no longer contain the old client implementation role or client workitem kinds.
- Static web and combined browser/API delivery harnesses have been removed from the current runtime.
- Legacy server-rendered product view files and product static asset delivery directories are absent.
- API mock and API SQLite run profiles remain available.
- Requirement, design, development, testing, manifest, replay verifier, delivery readiness, and requirement coverage currently use backend/API validation semantics.
- Board-related server code exposes JSON state and control APIs.
- Task Center supports assignment lifecycle, claim/return, context handoff, audit, maintenance, and API/CLI access.
- `frontend/` is the platform management, observability, and human-control web console.

## Current Limitations

- Customer-project business frontend generation is not currently implemented.
- Frontend development Agent roles, browser acceptance, static Web validation, and full-stack delivery profiles are not currently active.
- The current rule-based Planner is intentionally biased toward backend/API workitems.
- Demo mode uses deterministic local fixtures and must not be described as production execution.

## Validation Performed

```powershell
python -m compileall -q app conductor tests
python -c "import app.board; import app.human_control; from conductor.controller.engine import ConductorEngine; from conductor.execution.planner import Planner; from conductor.execution.runner import Runner; print('runtime imports ok')"
python -m pytest tests/test_execution_config.py tests/test_system_config.py tests/test_harness.py tests/test_scope_contract.py tests/test_board_api.py -q
```

## Documentation Rule

When updating documentation, describe backend/API focus as the current implementation state. Do not restate it as Conductor's permanent product identity unless a future explicit product decision says so.
