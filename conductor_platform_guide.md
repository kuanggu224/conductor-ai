# Conductor Platform Guide

Conductor is a backend/API orchestration platform.

## Runtime Model

The platform coordinates these stages:

1. Requirement: clarify scope and freeze executable requirements.
2. Design: produce backend/API design, data boundaries, and test strategy.
3. Development: execute backend/API implementation workitems.
4. Testing: collect endpoint, status, payload, persistence, and contract-test evidence.
5. Release readiness: audit manifests, reports, scope contract, and delivery evidence.

## Roles

Supported roles:

- `requirement_designer`
- `designer`
- `solution_designer`
- `backend_engineer`
- `tester`

## Workitem Kinds

Supported workitem kinds:

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

Supported execution paths:

- mock planning/execution fallback
- ShellHarness
- LLMHarness
- Agent CLI
- API mock delivery
- API SQLite delivery

## Control Surface

Project state, task claims, artifact content, settings, run actions, maintenance, and human-control operations are exposed through JSON APIs and CLI commands.

## Quality Signals

Quality is judged from artifacts, manifests, verifier output, benchmark reports, scope contract checks, delivery readiness, and concrete API validation evidence.
