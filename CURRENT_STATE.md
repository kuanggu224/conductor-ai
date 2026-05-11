# Conductor Current State

Last updated: 2026-05-11

## Stable Checkpoint

- Latest checkpoint commit: `4cdb012` (`Document current platform checkpoint`).
- Current full test result: `python -m pytest -q` -> `503 passed`.
- Current focus: backend orchestration, auditability, logs, manifest/replay, and task-center reliability.
- Frontend Board exists, but visual redesign is intentionally not the current priority.

## What Works

- Project execution flow is modeled as `requirement -> design -> development -> testing`.
- Requirement stage supports LLM-backed collaboration, review, revision, quality gates, frozen requirement artifacts, and benchmark comparison against direct LLM baselines.
- Agents can be activated by role and can use different execution backends, including LLMHarness, Agent CLI, ShellHarness, and StaticWebHarness.
- Task Center supports claim/return semantics, claim tokens, stale release, failure state, blockers, retry limits, and resume cursor generation.
- Run Manifest records project status, WorkItems, executions, artifacts, agents, CLI/LLM runs, collaboration sessions, retry history, preflight status, scope contract checks, token usage, cost estimates, and audit file paths.
- Manifest verifier now checks a broad set of consistency rules: ids, counts, state/cursor semantics, artifact lineage, task prompts, agent references, CLI/LLM run evidence, requirement quality, scope contracts, preflight gate, token usage, and cost totals.
- Replay trace can reconstruct a read-only project timeline from a manifest without rerunning agents.

## Known Gaps

- Development and testing stages are not yet as productized as the requirement stage.
- End-to-end real project success is not yet stable enough to call production-grade.
- Dynamic multi-agent collaboration exists, but its cost/benefit still needs more real-case benchmark runs.
- Local model and external CLI stability still depends on local configuration, model context length, timeout settings, and tool authorization.
- Frontend Board is useful for inspection but should not drive the next engineering phase.

## Common Commands

Run tests:

```powershell
python -m pytest -q
```

Run a project:

```powershell
python -m app.run_project --project-root <project-root> --requirement "Build a small static web app"
```

Resume a project:

```powershell
python -m app.run_project --project-root <project-root> --resume-project-id <project-id>
```

Preflight only:

```powershell
python -m app.run_project --project-root <project-root> --requirement "Build a small static web app" --preflight-only
```

Verify a manifest:

```powershell
python -m app.verify_manifest <project-root>\.conductor\manifests\<project-id>.manifest.json --fail-on-warnings
```

## Next Priorities

1. Run one small real end-to-end static web project through the platform and inspect manifest, report, artifacts, and replay trace.
2. Strengthen development-stage handoff from frozen requirement and design artifacts into implementation WorkItems.
3. Strengthen testing-stage behavior so test failures reliably create actionable development rework.
4. Add or improve health checks for configured LLM/CLI backends: server status, model availability, context length, timeout, and encoding.
5. Keep frontend changes paused unless a backend API shape blocks inspection.
