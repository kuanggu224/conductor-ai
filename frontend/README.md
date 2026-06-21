# Conductor Frontend

C3 dependency-risk control UI for the Conductor API platform.

Run locally:

```powershell
cd C:\99_self\conductor\conductor-ai
python -m uvicorn app.board:app --host 127.0.0.1 --port 8000
python -m http.server 4176 -d frontend
```

Open:

```text
http://127.0.0.1:4176
```

The frontend uses `http://127.0.0.1:8000` by default. Override with:

```text
http://127.0.0.1:4176/?api=http://127.0.0.1:8000
```

Coverage:

- `Dependency Graph`: project health, task dependencies, artifacts, agent nodes, human gate, operation console.
- `Tasks`: list, context, claim, claim next, batch claim, complete, fail, heartbeat, release, release stale/expired, sweep.
- `Agents`: roster, dynamic activations, claimable task listing, agent-side claim.
- `Review`: artifact detail, pause/resume, approval request, approve/reject/override.
- `Logs`: recent events, routes, executions, runtime stream refresh.
- `Settings`: API base, execution settings, CLI settings, LLM settings/preflight, diagnostics.
- `Todos`: list, create, read, update, delete.
