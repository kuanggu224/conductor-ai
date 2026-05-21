# Conductor Current State

Last updated: 2026-05-21

## Stable Checkpoint

- Latest verified code checkpoint: `979e23e` (`Map testing failures to checklist feedback`).
- Current full test result: `python -m pytest -q` -> `623 passed in 66.38s`.
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
- Requirement coverage and StaticWebHarness now cover file import/upload flows with browser evidence that a sample file was processed.
- Static web delivery now adds a real CSV import control only when frozen requirements ask for file import/upload, and StaticWebHarness distinguishes export/download buttons from import/upload controls.
- Requirement coverage now includes API endpoint behavior for backend/API requirements, and API validation WorkItems can satisfy it with explicit endpoint-behavior evidence.
- Testing-stage feedback rework has a project-level cap, preventing infinite development/testing loops.
- Testing-stage feedback rework now persists explicit inputs on the rework WorkItem and Task Center assignment: failed test artifacts, original implementation artifacts, frozen requirement, and design artifacts.
- Testing-stage feedback rework now embeds structured failure feedback extracted from failed test reports: failure signals, missing requirement coverage, exit code, and suggested fix directions.
- Task Center context now exposes the same structured testing feedback under `rework_context.testing_feedback` and renders it in Markdown prompts for external CLI/Agent workers.
- Structured testing feedback lookup is centralized in `conductor.testing.failure_feedback`, reducing drift across Task Center, manifest, project report, and replay outputs.
- Task Center context now includes a machine-readable `delivery_contract` and renders it into CLI prompts, making each external Agent's expected outputs, guardrails, and verification focus explicit.
- The same delivery contract is shared by direct Runner prompts for Agent CLI document/code execution, keeping internal execution and external Task Center handoff aligned.
- Code execution reports, project reports, and manifest execution records now include acceptance trace evidence that maps WorkItem acceptance criteria to validation status and changed files.
- Testing WorkItems now carry a machine-readable `testing_checklist` derived from frozen requirement coverage rules, including rule ids, labels, requirement signals, and required evidence terms.
- Structured testing feedback now maps missing coverage back to `missing_checklist_items`, so development rework prompts can cite the exact checklist `rule_id` and required evidence that failed.
- Development feedback rework now promotes missing checklist evidence into WorkItem acceptance criteria and Task Center prompts, making the repair target auditable before the next test pass.
- Run Manifest schema is now `1.36`; WorkItem and retry history records include relationship fields, structured testing feedback, and testing checklists for rework audit/replay; execution records include delivery contracts plus acceptance traces.
- Human Control now has CLI and Board API control paths, Board snapshot exposure, Manifest records, and Markdown project report audit output.
- TL dynamic team planning now adds a `rework_acceptance_guard` tester seat when development feedback rework carries explicit missing checklist evidence targets.
- Task Center Board API now covers dynamic Agent task discovery/claiming, batch claim, explicit lease renewal, expired lease release, stale release, and combined sweep maintenance.
- Manifest verification now validates execution delivery contracts and acceptance traces, including required input artifact references, list-shaped fields, trace status values, and evidence field types.
- Manifest verification also validates WorkItem testing checklist structure so malformed checklist fields are surfaced before replay/resume.
- Project reports render structured testing feedback under WorkItems, including failing checks, missing coverage, and suggested fixes.
- Replay trace renders WorkItem rework lineage and structured testing feedback, so failed-test-to-rework chains are visible in read-only replay output.
- Default ShellHarness validation skips real test commands when no project deliverables exist, especially when the project root is the Conductor source checkout.
- Manifest verification accepts intentionally reclassified failed test WorkItems when their failures have been flowed back into development rework.
- Platform diagnostics now expose structured UTF-8 readiness, LLM timeout status, timeout warnings, and block model probing when an enabled backend has an invalid timeout.

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
- Local model and external CLI stability still depends on local configuration, provider availability, model context length, timeout settings, and tool authorization.
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
4. Continue improving health checks for configured LLM/CLI backends, especially provider-specific failures and CLI authorization state.
5. Keep frontend changes paused unless a backend API shape blocks inspection.
