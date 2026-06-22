# Conductor Platform Guide

Conductor's long-term product positioning is an AI project execution platform for the full software project lifecycle. The current implementation is maintained around a backend/API execution lane while the control layer, Task Center, verification, and audit paths mature.

## Core Flow

Current active flow:

- Freeze requirements.
- Produce backend/API design and feature-slice plans.
- Assign backend/API implementation workitems.
- Validate API behavior with endpoint/status/payload and persistence evidence.
- Generate manifests, reports, replay verification output, and delivery readiness checks.

## Supported Roles

Currently active roles:

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

`full_cli` currently covers the active design/backend/test roles. It does not mean full-stack project delivery is implemented.

## Supported Harnesses

- ShellHarness
- LLMHarness
- Agent CLI execution
- API validation through generated tests and contract evidence

## Current Paused Surface

Legacy product view rendering, product static asset delivery, static web validation, combined browser/API delivery, and client implementation role/workitem kinds are removed from the current codebase.

This paused surface should be treated as current implementation status. Future frontend or full-stack delivery must be deliberately redesigned and connected to the same control, task, artifact, validation, and audit systems.
