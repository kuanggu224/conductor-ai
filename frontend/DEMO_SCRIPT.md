# Conductor Demo Script

Use this script for a controlled platform demonstration when external Agent CLI or LLM bindings are not guaranteed.

## Start

```powershell
cd C:\99_self\conductor\conductor-ai
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\demo-start.ps1 -Open
```

Open:

```text
http://127.0.0.1:4176/?demo=1
```

`demo-start.ps1` runs the same frontend preflight, including static asset smoke checks, before serving the page.

Expected first screen:

- Project selector shows `Demo / Build an API-only todo service...`.
- Top status is `In Progress`.
- Risk panel shows `Medium`, `At Risk / 78`, `Claimable 1`, `Blocked 1`.
- Dependency graph shows requirement, design, development, testing, delivery, task assignment, agent and artifact nodes.

## Capability Boundaries

- Demo mode is deterministic and offline: it does not require the backend, Agent CLI, or external LLM provider.
- Demo mode exercises the operator console, dependency graph, task handoff, agents, artifacts, settings diagnostics, logs, and Todo CRUD locally.
- The `Settings` page shows `Capability Alignment`: each demo area is paired with the live API or runtime dependency it represents.
- In Live mode, `Check Backend` calls `/api/status` to confirm the configured API is online before project actions.
- Live mode should be used only when the backend is running at the configured API URL.
- The broader backend regression subset is covered by `demo-check.ps1 -StaticSmoke -FullBackendChecks`.

## Talk Track

1. Dependency Graph
   - Explain that the graph is the operator view of the platform: stages, WorkItems, task assignments, agents, artifacts and human gate.
   - Point at `Risk Control` to show readiness, blockers and claimable work.
   - Open `Live State` to show the current project payload.

2. Task Center
   - Click `Tasks`.
   - Open `Context` for `assignment-api-validation`.
   - Explain that context packages the frozen requirement, required inputs and expected outputs for an Agent.
   - Click `Claim Next` to simulate an Agent taking the next claimable task.

3. Progression
   - Return to `Dependency Graph`.
   - Click `Step`.
   - Expected signal: readiness moves to `At Risk / 92`, risk becomes `Low`, blockers become `0`.
   - Click `Run`.
   - Expected signal: project status becomes `Ready`, readiness becomes `Ready / 96`, `Approve` becomes enabled.

4. Review
   - Click `Review`.
   - Open `FastAPI SQLite Implementation`.
   - Explain that the artifact records changed files, pytest contract validation and SQLite evidence.
   - Open `API Contract Validation` to show endpoint evidence and delivery readiness.

5. Live Mode
   - Click `Settings` and point at `Capability Alignment` to separate deterministic demo behavior from live backend/Agent/LLM requirements.
   - If switching to Live mode, click `Check Backend` first; expect `Online` before running `Step` or `Run`.
   - Click `Live` in the top bar only when the backend is running at the configured API URL.
   - If backend is not running, stay in Demo mode for the presentation.

## Fallbacks

- If the project list is empty, use `?demo=1`.
- If the Live API error banner appears, click `Load Demo` for the offline presentation path or `Settings` to check the backend URL.
- If an operation returns a backend error, click `Demo` to reload the local fixture.
- If port `4176` is in use, start with another port: `.\scripts\demo-start.ps1 -Port 4177`.
- If preflight port `4178` is in use, start with another preflight port: `.\scripts\demo-start.ps1 -PreflightSmokePort 4179`.
- If the graph looks too wide, refresh the page; the responsive layout is verified for desktop and 390px mobile width.

## Verification

Before a demo run:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\demo-check.ps1 -StaticSmoke
```

For the broader API delivery regression subset:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\demo-check.ps1 -StaticSmoke -FullBackendChecks
```
