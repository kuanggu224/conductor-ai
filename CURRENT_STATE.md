# Conductor Current State

Last updated: 2026-05-11

## Stable Checkpoint

- Latest verified code checkpoint: `0c647d1` (`Structure testing feedback for rework`).
- Current full test result: `python -m pytest -q` -> `518 passed in 43.53s`.
- Current focus: backend orchestration, auditability, logs, manifest/replay, and task-center reliability.
- Frontend Board exists, but visual redesign is intentionally not the current priority.

## Current Stabilization Notes

- Mock/offline project execution is isolated from configured real LLM backends unless `llm_harness` is explicitly selected.
- Mock requirement collaboration now produces a structured requirement draft that can pass the offline quality gate.
- Downstream WorkItems now persist context-selected `input_artifact_ids`, so frozen requirements and design artifacts are explicit in state, Task Center, manifest, and resume flows.
- Scope contract checks no longer treat local browser APIs or localStorage write consistency as backend/cloud scope expansion.
- StaticWebHarness can exercise common input + button UIs without a `<form>`, and now records interaction, localStorage persistence, reload, and export evidence for these pages.
- StaticWebHarness and LLM-generated file checks now reject common UTF-8/GBK mojibake patterns in HTML/JS/CSS artifacts while preserving normal Chinese UI text.
- Requirement coverage no longer treats input sanitization wording such as filtering newline characters as a filter/search UI requirement.
- Testing-stage feedback rework has a project-level cap, preventing infinite development/testing loops.
- Testing-stage feedback rework now persists explicit inputs on the rework WorkItem and Task Center assignment: failed test artifacts, original implementation artifacts, frozen requirement, and design artifacts.
- Testing-stage feedback rework now embeds structured failure feedback extracted from failed test reports: failure signals, missing requirement coverage, exit code, and suggested fix directions.
- Default ShellHarness validation skips real test commands when no project deliverables exist, especially when the project root is the Conductor source checkout.
- Manifest verification accepts intentionally reclassified failed test WorkItems when their failures have been flowed back into development rework.

## Latest Real E2E Smoke

- Project root: `C:\99_self\conductor_test\static-reading-list-llm-harness-fast-20260511-r3`.
- Command profile: `code_cli` with `--llm-harness cloud`, no Agent CLI binding.
- Final status: `completed`.
- Generated files: `index.html`, `static/app.js`, `static/style.css`.
- Verification: manifest, manifest verification, replay trace, and audit bundle all passed with 0 warnings.
- StaticWebHarness evidence covered add interaction, localStorage persistence, reload persistence, and export/download.
- Follow-up encoding audit confirmed the generated files contain valid UTF-8 Chinese text; garbled PowerShell output was a terminal display issue, not artifact corruption.
- A synthetic corrupted JS artifact now fails StaticWebHarness with `script asset appears to contain mojibake/corrupted UTF-8 text`.

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

1. Inspect the latest real E2E smoke artifacts and identify remaining product gaps in development/testing quality.
2. Strengthen development-stage handoff from frozen requirement and design artifacts into implementation WorkItems.
3. Strengthen testing-stage behavior so failed requirement coverage produces more precise, actionable rework prompts.
4. Add or improve health checks for configured LLM/CLI backends: server status, model availability, context length, timeout, and encoding.
5. Keep frontend changes paused unless a backend API shape blocks inspection.
