# Conductor Frontend

C3 dependency-risk control UI for the Conductor API platform.

Run locally:

```powershell
cd C:\99_self\conductor\conductor-ai
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\start.ps1 -Open
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\stop.ps1
```

Run the offline presentation demo:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\demo-start.ps1 -Open
```

`demo-start.ps1` runs the frontend preflight, including static asset smoke checks, before starting the demo server. If the temporary smoke port is occupied, pass `-PreflightSmokePort 4179`.

Open:

```text
http://127.0.0.1:4176/?demo=1
```

The frontend uses `http://127.0.0.1:8000` by default. Override with:

```text
http://127.0.0.1:4176/?api=http://127.0.0.1:8000
```

Offline demo mode:

```text
http://127.0.0.1:4176/?demo=1
```

Demo mode loads a complete API-only delivery project locally, so the dependency graph, task center, agents, review artifacts and logs remain present even when the backend is not running. Use the top-bar `Demo` / `Live` toggle to switch modes. The `Settings` page includes a capability alignment panel that maps deterministic demo behavior to the corresponding live API or runtime dependency. In Live mode, use `Check Backend` to confirm the configured API URL responds before running project actions.

For a step-by-step presentation flow, use `frontend/DEMO_SCRIPT.md`.

Preflight:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\demo-check.ps1 -StaticSmoke
```

Coverage:

- `Dependency Graph`: project health, task dependencies, artifacts, agent nodes, human gate, operation console.
- `Tasks`: list, context, claim, claim next, batch claim, complete, fail, heartbeat, release, release stale/expired, sweep.
- `Agents`: roster, dynamic activations, claimable task listing, agent-side claim.
- `Review`: artifact detail, pause/resume, approval request, approve/reject/override.
- `Logs`: recent events, routes, executions, runtime stream refresh.
- `Settings`: live API configuration, backend status checks in Live mode; offline runtime, diagnostics, LLM preflight evidence, and Demo/Live capability alignment in Demo mode.
- `Todos`: list, create, read, update, delete.
