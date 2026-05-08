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

## Task Center

The Task Center is the lightweight coordination boundary for external agents.
It is not a distributed queue yet, but it provides a stable claim/return
protocol over persisted `.conductor/state` files and the Board API.

CLI entrypoint:

```bash
python -m app.task_center list --project-root <project-root>
python -m app.task_center summary --project-root <project-root>
python -m app.task_center list --project-root <project-root> --stale-only --stale-after-seconds 3600
python -m app.task_center context <assignment-id> --project-root <project-root>
python -m app.task_center context <assignment-id> --project-root <project-root> --format markdown
python -m app.task_center context <assignment-id> --project-root <project-root> --prompt-file .conductor/task_center/prompts/task.md
python -m app.task_center claim-next --project-root <project-root> --agent-id <agent-id> [--role <role>]
python -m app.task_center claim-next --project-root <project-root> --agent-id <agent-id> --with-context
python -m app.task_center claim-next --project-root <project-root> --agent-id <agent-id> --with-context --context-format markdown
python -m app.task_center claim-next --project-root <project-root> --agent-id <agent-id> --prompt-file .conductor/task_center/prompts/next-task.md
python -m app.task_center claim <assignment-id> --project-root <project-root> --agent-id <agent-id>
python -m app.task_center complete <assignment-id> --project-root <project-root> --result-summary "done"
python -m app.task_center complete <assignment-id> --project-root <project-root> --output-file result.md
python -m app.task_center fail <assignment-id> --project-root <project-root> --blocked-reason "reason"
python -m app.task_center release <assignment-id> --project-root <project-root> --release-reason "worker interrupted"
python -m app.task_center release-stale --project-root <project-root> --stale-after-seconds 3600 --release-reason "stale cleanup"
```

Board API endpoints:

- `GET /api/projects/{project_id}/tasks`
- `GET /api/projects/{project_id}/tasks?stale_only=true&stale_after_seconds=3600`
- `GET /api/projects/{project_id}/tasks/summary`
- `GET /api/projects/{project_id}/tasks/{assignment_id}/context`
- `POST /api/projects/{project_id}/tasks/claim-next`
- `POST /api/projects/{project_id}/tasks/{assignment_id}/claim`
- `POST /api/projects/{project_id}/tasks/{assignment_id}/complete`
- `POST /api/projects/{project_id}/tasks/{assignment_id}/fail`
- `POST /api/projects/{project_id}/tasks/{assignment_id}/release`
- `POST /api/projects/{project_id}/tasks/release-stale`

Task payloads expose `claimable` and `unmet_dependency_ids`. Summaries expose
`total`, `queued`, `claimed`, `completed`, `failed`, `blocked`, `claimable`,
`blocked_by_dependencies`, and `stale_claimed`. Run manifests and project
reports also include Task Center readiness for audit and replay.

## Diagnostics

Use diagnostics before real CLI or LLM-backed runs to verify local bindings and
runtime configuration:

```bash
python -m app.diagnostics
python -m app.diagnostics --probe-cli
python -m app.diagnostics --preflight-llm
python -m app.run_project --diagnose
python -m app.run_project --diagnose --diagnose-cli
python -m app.run_project --diagnose --diagnose-llm
```

Board exposes the same health snapshot for frontends:

- `GET /api/diagnostics`
- `GET /api/diagnostics?probe_cli=true`
- `GET /api/diagnostics?probe_llm=true`
- `GET /api/diagnostics?preflight_llm=true`

The default API call is read-only and does not touch network services or start
agent CLIs. The `probe_cli=true` variant runs lightweight `--version` checks.
The `probe_llm=true` variant checks enabled OpenAI-compatible model endpoints.
The `preflight_llm=true` variant also runs a lightweight chat-completion probe.
LLM diagnostics include the configured model, timeout, available model list,
detected context length, whether the selected model is listed, a compact health
status, and a remediation recommendation.

`python -m app.run_project` runs a preflight gate before real Agent execution.
For `design_cli_only`, `code_cli`, `full_cli`, or explicit `--llm-harness`
runs, the gate checks the selected CLI/LLM backend and fails before creating a
project if no real backend is usable. Use `--skip-preflight-gate` only for
controlled offline tests. Gate results are persisted at
`.conductor/diagnostics/run-preflight/preflight-gate.json` under the resolved
project root. Successful run manifests index the same file as
`files.preflight_gate` and summarize `summary.preflight_gate_ok` /
`summary.preflight_gate_errors`.

### Jiutian LLM Backend

Conductor can use Jiutian through the existing OpenAI-compatible cloud backend.
Keep the real API key only in `.conductor/llm.config.json`, which is ignored by
Git.

The Board LLM settings page exposes provider presets for OpenAI, Jiutian, and
LM Studio. The API reports only whether a key is present; it does not return the
stored key. Leaving the key field blank while saving preserves the existing
local key.

Minimal cloud config:

```json
{
  "cloud": {
    "cloud_llm_base_url": "https://jiutian.10086.cn/largemodel/moma/api/v3",
    "cloud_llm_model": "jiutian-lan-comv3",
    "cloud_llm_api_key": "<fill locally>",
    "cloud_llm_timeout": 120.0,
    "cloud_llm_enabled": true
  },
  "usage": {
    "runner_enabled": true,
    "preferred_backend": "cloud"
  }
}
```

Validate the configured key without printing it:

```powershell
python -m app.requirement_benchmark preflight --backend cloud --output-dir .conductor\diagnostics\jiutian-preflight
```

Run one platform-vs-direct requirement check with the cloud backend:

```powershell
python -m app.requirement_benchmark run-suite --cases reading_list --output-dir .conductor\diagnostics\jiutian-reading-list --platform-llm cloud --direct-llm cloud --direct-prompt-mode plain --max-steps 4 --collaboration-max-rounds 1 --static-requirement-review
```

### Windows PowerShell UTF-8

If Chinese text appears as mojibake when reading logs or reports in PowerShell,
enable UTF-8 for the current shell before running Conductor commands:

```powershell
. .\scripts\windows-utf8.ps1
```

Conductor Python entrypoints configure UTF-8 stdio automatically, and the shell
harness passes UTF-8 defaults to Python-based child processes:

- `PYTHONUTF8=1`
- `PYTHONIOENCODING=utf-8`
- `LANG=C.UTF-8`
- `LC_ALL=C.UTF-8`

The PowerShell script is still useful for commands such as `Get-Content`,
`type`, and terminal log tailing. On Windows PowerShell 5, `Get-Content`
otherwise defaults to the ANSI code page unless `-Encoding utf8` is specified.

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

Current full verification after LLM settings and manifest redaction hardening:
`333 passed` with `python -m pytest -q`.

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
