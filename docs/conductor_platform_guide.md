# Conductor Platform Guide

Conductor is maintained as a backend/API-only orchestration platform.

## Core Flow

- Freeze requirements.
- Produce backend/API design and feature-slice plans.
- Assign backend/API implementation workitems.
- Validate API behavior with endpoint/status/payload and persistence evidence.
- Generate manifests, reports, replay verification output, and delivery readiness checks.

## Supported Roles

- `requirement_designer`
- `designer`
- `solution_designer`
- `backend_engineer`
- `tester`

## Supported Delivery Profiles

- `mock`
- `api_mock`
- `api_sqlite`
- `design_cli_only`
- `code_cli`
- `full_cli`

## Supported Harnesses

- ShellHarness
- LLMHarness
- Agent CLI execution
- API validation through generated tests and contract evidence

## Removed Surface

Legacy product view rendering, static asset delivery, static web validation, and combined browser/API delivery are removed from the current platform scope.
