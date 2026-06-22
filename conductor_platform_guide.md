# Conductor Platform Guide

Conductor is an AI project execution and multi-agent collaboration platform for software delivery. This guide describes the current runtime model, which is presently concentrated on backend/API execution and validation.

## Runtime Model

The current platform coordinates these stages:

1. Requirement: clarify scope and freeze executable requirements.
2. Design: produce backend/API design, data boundaries, and test strategy.
3. Development: execute backend/API implementation workitems.
4. Testing: collect endpoint, status, payload, persistence, and contract-test evidence.
5. Release readiness: audit manifests, reports, scope contract, and delivery evidence.

This current stage model should be read as the active implementation baseline, not as a permanent exclusion of frontend, full-stack, UI design, operations, data, or documentation engineering work.

## Roles

Currently supported execution roles:

- `requirement_designer`
- `designer`
- `solution_designer`
- `backend_engineer`
- `tester`

Frontend/project UI delivery roles are not active in the current runtime.

## Workitem Kinds

Currently supported workitem kinds:

- `requirement_spec`
- `design_overview`
- `feature_slice_plan`
- `api_design`
- `test_design`
- `api_implementation`
- `data_implementation`
- `generic_implementation`
- `acceptance_check`
- `automated_test`
- `api_validation`

## Execution Paths

Currently supported execution paths:

- mock planning/execution fallback
- ShellHarness
- LLMHarness
- Agent CLI
- API mock delivery
- API SQLite delivery

## Control Surface

Project state, task claims, artifact content, settings, run actions, maintenance, and human-control operations are exposed through JSON APIs and CLI commands. The `frontend/` web console consumes this platform control surface; it is not the same thing as customer-project frontend delivery.

## Quality Signals

Quality is currently judged from artifacts, manifests, verifier output, benchmark reports, scope contract checks, delivery readiness, and concrete API validation evidence.

Future delivery types should add their own concrete validation evidence without bypassing the control layer, Task Center, artifact store, manifest, replay, and audit flows.
