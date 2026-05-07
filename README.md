# Conductor

An observable AI task routing and multi-expert orchestration system.

This workspace currently contains the Sprint 1 minimal runnable core:

- Core domain models for `Project`, `Stage`, `WorkItem`, `Execution`, `Agent`, `RouteDecision`, `SharedProjectState`, `GlobalMemory`, and `ContextPack`
- A default workflow template with `design -> development -> testing`
- A rule-driven `LeadController` that can initialize a project and advance it through mock execution
- An in-memory shared state store
- A lightweight runner with mock, harness, and CLI-backed execution paths

## Simple To-do App

The workspace also includes a standalone in-memory to-do application:

- API root: `app.todo_app`
- Start it with: `python -m app.todo_app`
- Or run the package entrypoint with: `python -m app`
- The package entrypoint proxies to the to-do app
- Endpoints:
  - `GET /`
  - `GET /health`
  - `GET /api/todos`
  - `POST /api/todos`
  - `GET /api/todos/{todo_id}`
  - `PATCH /api/todos/{todo_id}`
  - `DELETE /api/todos/{todo_id}`

## Current Run Mode

The repository is configured for local, deterministic execution.

- No live LLM calls are required for the default flow
- No real CLI agent execution is required for the default flow
- Tests use mock or stubbed backends
- System configuration can be saved and loaded from `conductor/config/system.config.json`

## How To Run

```bash
python -m pytest -q
```

### Windows PowerShell UTF-8

If Chinese text appears as mojibake when reading logs or reports in PowerShell,
enable UTF-8 for the current shell before running Conductor commands:

```powershell
. .\scripts\windows-utf8.ps1
```

Conductor Python entrypoints also configure UTF-8 stdio automatically. The
PowerShell script is still useful for commands such as `Get-Content`, `type`,
and terminal log tailing. On Windows PowerShell 5, `Get-Content` otherwise
defaults to the ANSI code page unless `-Encoding utf8` is specified.

Verified with the current workspace test suite on April 16, 2026.

## What To Expect

The default flow is:

1. Input a requirement
2. Create a `Project`
3. Enter the `design` stage
4. Generate at least one `WorkItem`
5. Run the work item through the mock `Runner`
6. Update shared state
7. Let `LeadController` decide the next action

Current test status in this workspace: `80 passed` with `python -m pytest -q`.

## Project Layout

- `conductor/domain/`
- `conductor/controller/`
- `conductor/workflow/`
- `conductor/execution/`
- `conductor/agents/`
- `conductor/state/`
- `conductor/memory/`
- `conductor/context/`
- `conductor/board/`
- `tests/`
