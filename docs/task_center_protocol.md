# Task Center Protocol

Task Center is Conductor's lightweight coordination boundary for external
agents. It stores `TaskAssignment` records in `SharedProjectState` and keeps
those records synchronized with the corresponding `WorkItem` lifecycle.

It is intentionally not a distributed queue yet. The current goal is a stable,
auditable claim/return protocol that works from the Board API, local CLI, run
manifest, project report, and Board snapshot.

## Lifecycle

Task assignment statuses:

- `queued`: available if all dependencies are completed.
- `claimed`: assigned to an agent and synchronized to `WorkItem.running`.
- `completed`: returned successfully and synchronized to `WorkItem.done`.
- `failed`: returned unsuccessfully and synchronized to `WorkItem.failed`.
- `blocked`: reserved for dependency or policy blocking.
- `release`: claimed/failed assignments can be released back to `queued`.
- `heartbeat`: claimed assignments can refresh worker activity without changing
  WorkItem status.

Claim rules:

- `claim` and `claim-next` only allow `queued` assignments.
- Direct `claim` also validates dependency readiness.
- `claim-next` skips assignments with unmet dependencies.
- Failed dependency checks report `unmet_dependency_ids`.
- Stale detection uses `last_heartbeat_at` when present, falling back to
  `claimed_at`.
- File-backed Task Center mutations acquire a per-project `.lock` file and
  refresh state from disk before claim/return/heartbeat/release transitions.
  This reduces duplicate claims when multiple worker processes use the same
  `.conductor/state` directory.
- Corrupt persisted `*.state.json` files are quarantined as
  `*.state.json.corrupt-*` during `FileStateStore` startup so other valid
  project states can still load.

## CLI

Use the persisted project root:

```bash
python -m app.task_center summary --project-root <project-root>
python -m app.task_center list --project-root <project-root>
python -m app.task_center list --project-root <project-root> --status queued
python -m app.task_center list --project-root <project-root> --stale-only --stale-after-seconds 3600
python -m app.task_center context <assignment-id> --project-root <project-root>
python -m app.task_center context <assignment-id> --project-root <project-root> --format markdown
python -m app.task_center context <assignment-id> --project-root <project-root> --prompt-file .conductor/task_center/prompts/task.md
python -m app.task_center agents-for <assignment-id> --project-root <project-root>
python -m app.task_center tasks-for-agent <agent-id> --project-root <project-root> --claimable-only
python -m app.task_center claim-for-agent <agent-id> --project-root <project-root> --with-context
python -m app.task_center claim-for-agent <agent-id> --project-root <project-root> --with-context --prompt-file .conductor/task_center/prompts/agent-task.md
python -m app.task_center claim-next --project-root <project-root> --agent-id <agent-id> --role backend_engineer
python -m app.task_center claim-next --project-root <project-root> --agent-id <agent-id> --with-context
python -m app.task_center claim-next --project-root <project-root> --agent-id <agent-id> --with-context --context-format markdown
python -m app.task_center claim-next --project-root <project-root> --agent-id <agent-id> --prompt-file .conductor/task_center/prompts/next-task.md
python -m app.task_center claim <assignment-id> --project-root <project-root> --agent-id <agent-id> --with-context
python -m app.task_center complete <assignment-id> --project-root <project-root> --agent-id <agent-id> --claim-token <claim-token> --result-summary "done"
python -m app.task_center complete <assignment-id> --project-root <project-root> --agent-id <agent-id> --claim-token <claim-token> --output-file result.md
python -m app.task_center fail <assignment-id> --project-root <project-root> --agent-id <agent-id> --claim-token <claim-token> --blocked-reason "reason"
python -m app.task_center heartbeat <assignment-id> --project-root <project-root> --agent-id <agent-id> --claim-token <claim-token>
python -m app.task_center release <assignment-id> --project-root <project-root> --agent-id <agent-id> --claim-token <claim-token> --release-reason "worker interrupted"
python -m app.task_center release-stale --project-root <project-root> --stale-after-seconds 3600 --release-reason "stale cleanup"
```

Use `--state-dir` when the state directory is not under
`<project-root>/.conductor/state`.

Use `context --format markdown` when handing a claimed assignment to a CLI
agent. The Markdown output is prompt material: it includes project goal,
assignment metadata, WorkItem details, acceptance criteria, selected input
artifact content, and the return protocol.
Use `claim` or `claim-next` with `--with-context --context-format markdown`
when the worker should claim the task and receive a direct Markdown prompt in a
single command.
Use `agents-for` to see which dynamic Agent activations are eligible for a
specific assignment. Use `tasks-for-agent` to list assignments that match one
dynamic Agent activation. Use `claim-for-agent` when a dynamically activated
Agent should claim the next matching task without separately passing role and
assignment id. `claim-for-agent --with-context --prompt-file <path>` is the
recommended handoff for external CLI agents because it claims the task, returns
the claim token, records the matched Agent seat, renders the task context, and
persists the Markdown prompt in one audited transition.
`tasks-for-agent` includes `claim_command` and `claim_with_context_command` for
claimable assignments; blocked assignments leave both command fields empty so
external agents do not accidentally claim work that is blocked by dependencies
or write-scope conflicts.
Use `--prompt-file <path>` to persist the rendered Markdown prompt for audit,
handoff, or direct CLI consumption. Relative paths are resolved under
`project_root`.
Markdown prompts include copyable `complete`, `fail`, and `release` commands
with the current `assignment_id` and `project_root`, so external agents can
return task status without reconstructing the protocol manually.
Claimed task payloads from the CLI, Board API, and context JSON include a
`return_commands` object with copyable `complete`,
`complete_with_output_file`, `fail`, `fail_with_output_file`, `heartbeat`, and
`release` commands. These commands include the current `agent_id` and
`claim_token` when the assignment is actively claimed; non-claimed tasks keep
`return_commands` empty. Board API task payloads also include `return_api_paths`
for the matching complete/fail/heartbeat/release endpoints.
CLI/API return paths validate claim status, `agent_id`, and `claim_token`
before creating an external output artifact, so stale-token or wrong-agent
returns do not leave orphan artifacts in project state.
Audit and maintenance reports also flag historical `task_center/external`
artifacts that are not referenced by any assignment `output_artifact_ids` as
`orphan_external_artifact`; these findings include `related_artifact_ids` so
Manifest verification can validate the referenced artifact.
Findings for missing input/output artifacts include `missing_artifact_ids`,
keeping absent ids machine-readable without requiring them to exist in archived
artifacts.
Eligible dynamic Agent entries in context payloads and Markdown prompts include
`claimable_for_agent` and `write_scope_conflict_assignment_ids`. External CLI
agents should treat `claimable_for_agent=false` as a hard handoff warning and
avoid starting work until the conflicting claimed assignments are returned or
released.
Context payloads also include a top-level `handoff_safety` object with
`ready_for_handoff`, `status`, `assignment_claimable`, unmet dependencies,
write-scope conflicts, warnings, and guidance. Markdown prompts render the same
summary under `## Handoff Safety`, so external workers can make a claim/no-claim
decision from the prompt without separately querying Task Center.

Expected CLI transition failures are emitted as machine-readable JSON on stderr:

- `ok=false`
- `error`
- `error_code`
- `status_code`
- optional `details`

For write-scope conflicts, `error_code=write_scope_conflict` and
`details.write_scope_conflict_assignment_ids` lists the claimed assignments that
must return or be released before the worker can safely claim the task.

## Board API

Endpoints:

- `GET /api/projects/{project_id}/tasks`
- `GET /api/projects/{project_id}/tasks?status=queued`
- `GET /api/projects/{project_id}/tasks?stale_only=true&stale_after_seconds=3600`
- `GET /api/projects/{project_id}/tasks/summary`
- `GET /api/projects/{project_id}/tasks/{assignment_id}/context`
- `GET /api/projects/{project_id}/tasks/{assignment_id}/context?format=markdown`
- `POST /api/projects/{project_id}/tasks/claim-batch`
- `POST /api/projects/{project_id}/tasks/claim-next`
- `POST /api/projects/{project_id}/tasks/{assignment_id}/claim`
- `POST /api/projects/{project_id}/tasks/{assignment_id}/complete`
- `POST /api/projects/{project_id}/tasks/{assignment_id}/fail`
- `POST /api/projects/{project_id}/tasks/{assignment_id}/heartbeat`
- `POST /api/projects/{project_id}/tasks/{assignment_id}/release`
- `POST /api/projects/{project_id}/tasks/release-stale`
- `POST /api/projects/{project_id}/tasks/release-expired-leases`
- `POST /api/projects/{project_id}/tasks/sweep`

Dynamic Agent task-list responses mirror the CLI handoff fields: claimable
tasks include `claim_command`, `claim_with_context_command`, and
`claim_api_path`; blocked tasks leave those fields empty.

`claim-next` request body:

```json
{
  "agent_id": "agent-backend",
  "role": "backend_engineer",
  "claim_reason": "external worker",
  "include_context": true,
  "include_context_content": true,
  "max_context_content_chars": 12000,
  "context_format": "json",
  "prompt_file": ".conductor/task_center/prompts/next-task.md",
  "lease_seconds": 3600
}
```

`claim-batch` accepts the same fields plus `limit`. It claims up to `limit`
currently claimable assignments, optionally filtered by `role`, and returns
`claimed_count`, `summary`, and `tasks`. The API uses the same configured bulk
claim cap as the CLI.

`complete` request body:

```json
{
  "agent_id": "agent-backend",
  "claim_token": "token-from-claim-response",
  "result_summary": "implemented",
  "output_artifact_ids": ["artifact-1"],
  "output_artifact_content": "# Result\n\nImplemented details.",
  "output_artifact_kind": "implementation_report",
  "output_artifact_title": "Backend implementation report"
}
```

`fail` request body:

```json
{
  "agent_id": "agent-backend",
  "claim_token": "token-from-claim-response",
  "result_summary": "validation failed",
  "blocked_reason": "missing dependency",
  "output_artifact_ids": []
}
```

`heartbeat` request body:

```json
{
  "agent_id": "agent-backend",
  "claim_token": "token-from-claim-response",
  "lease_seconds": 3600
}
```

`agent_id` and `claim_token` are optional. When provided, `agent_id` must match
the current claimant and `claim_token` must match the current claim token.
`lease_seconds` is optional on Board API claim and heartbeat requests. A
positive value sets or renews the explicit lease; `0` clears the lease on
claim, and heartbeat can pass `0` to remove an existing explicit lease.

`release` request body:

```json
{
  "agent_id": "agent-backend",
  "claim_token": "token-from-claim-response",
  "release_reason": "worker interrupted"
}
```

`agent_id` and `claim_token` are optional for `complete`, `fail`, `heartbeat`,
and `release`. When provided, they must match the current claimant and current
claim token. This is the recommended guard for external workers so one Agent
cannot accidentally return another Agent's task or return a stale prompt after a
task has been released and re-claimed.

`release` returns a claimed or failed assignment to `queued`, clears the current
agent, clears return timestamps and stale prompt metadata, and synchronizes the
WorkItem back to `pending`. Completed assignments cannot be released.

`release-stale` request body:

```json
{
  "stale_after_seconds": 3600,
  "release_reason": "stale cleanup"
}
```

`release-stale` bulk releases claimed assignments whose heartbeat age is greater
than or equal to the threshold, falling back to `claimed_at` when no heartbeat
exists. It returns `released_count`, `summary`, and the released task payloads.

`release-expired-leases` releases only assignments whose explicit
`lease_expires_at` is in the past. It is the API equivalent of
`python -m app.task_center release-expired-leases` and is safe for a Board
maintenance button or a lightweight watchdog.

`sweep` runs expired lease release first, then stale claim release. It accepts
`stale_after_seconds`, `expired_lease_release_reason`, and
`stale_release_reason`, and returns separate `expired_lease_tasks` and
`stale_tasks` lists.

When `output_artifact_content` is present, the platform creates and persists a
new artifact with source backend `task_center/external`, appends its id to
`output_artifact_ids`, and synchronizes the assignment and WorkItem return.

## Payload Shape

List responses include:

- `ok` for CLI responses
- `project_id`
- `status_filter`
- `total`
- `summary`
- `tasks`

Summary responses include:

- `ok` for CLI responses
- `project_id`
- `summary`

Context responses from `context` and `/context` include:

- `ok`
- `project_id`
- `project_goal`
- `project_root`
- `execution_brief`
- `frozen_requirement_baseline`
- `assignment`
- `workitem`
- `input_artifacts`
- `output_artifacts`

`input_artifacts[]` includes metadata and, by default, `content` read from the
artifact file path when available. Use CLI `--no-content` or API
`?include_content=false` when only metadata is needed.
`execution_brief` is a compact instruction block for external workers. It
summarizes the project, assignment, acceptance criteria, input artifacts, and
return protocol. When a `frozen_requirement_spec` is present, it is repeated in
`frozen_requirement_baseline` and called out in the brief as the controlling
contract for design, implementation, and testing.
CLI `context --format markdown` renders the same payload as a human-readable
task prompt for coding agents.
API `/context?format=markdown` returns the same prompt as `text/markdown`.

Mutation responses from `claim`, `claim-next`, `complete`, `fail`, `heartbeat`,
and `release` include:

- `ok` for CLI responses
- `project_id`
- `summary`
- `task`

External CLI mutations after `claim` are guarded. `complete`, `fail`,
`heartbeat`, and worker `release` must pass both `--agent-id` and the
`--claim-token` returned by the claim response. `release-stale` is an operator
cleanup action and can release expired claimed tasks without a worker token.

When `--with-context` or `include_context=true` is used on claim operations,
the response also includes `context`, with the same shape as the standalone
context endpoint.
When API claim operations set `context_format=markdown`, the response includes
`context_markdown` instead of `context`, while still returning `summary` and
`task`.
When API claim operations set `prompt_file`, the platform writes the rendered
Markdown prompt under `project_root` for relative paths, returns `prompt_file`,
and records the path on the `TaskAssignment`.
Prompt files must resolve inside `project_root`; paths that escape the project
root are rejected before claim state is mutated.
For CLI claim operations, `--context-format markdown` prints the rendered
Markdown prompt instead of the JSON mutation payload.

The mutation `summary` is computed after the state transition. Clients can use
it to update dashboards without issuing a second summary request.

Task payloads include:

- `id`
- `workitem_id`
- `role`
- `status`
- `assigned_agent_id`
- `claim_token`
- `claim_reason`
- `claimable`
- `unmet_dependency_ids`
- `claimed_age_seconds`
- `heartbeat_age_seconds`
- `lease_seconds`
- `lease_expires_at`
- `lease_expired`
- `stale_claimed`
- `dependencies`
- `input_artifact_ids`
- `output_artifact_ids`
- `result_summary`
- `blocked_reason`
- `claimed_at`
- `last_heartbeat_at`
- `returned_at`
- `prompt_file`

`input_artifact_ids` is not limited to direct WorkItem dependencies. For
controller-created assignments it also includes the ContextBuilder-selected
upstream artifacts, such as requirement baselines and design documents that the
agent should read before execution.
- `workitem`
- `artifacts`

Summary payloads include:

- `total`
- `queued`
- `claimed`
- `completed`
- `failed`
- `blocked`
- `claimable`
- `blocked_by_dependencies`
- `stale_claimed`

Multi-project `audit-all` and `maintenance` reports include operator rollups:

- `attention_project_ids`: project ids with at least one audit finding.
- `finding_code_counts`: finding code histogram across all audited projects.
- `recommendations`: de-duplicated remediation text from audit findings.

The compact `maintenance --latest-output` pointer and `maintenance-status`
payload preserve the same rollups so a scheduler, watchdog, or Board surface can
show which projects need attention without reading the full maintenance report.
`watchdog` is a one-shot scheduler/watchdog entrypoint: it reads the latest
pointer, runs `maintenance` when the pointer is missing, stale, invalid, or
unhealthy, writes the refreshed report/latest files, and returns both the
pre-check and final status payloads. Use `--check-only` for read-only probes.
`maintenance`, the latest pointer, and `maintenance-status` also include:

- `operator_guidance`: compact text describing how an operator/watchdog should
  use the maintenance loop.
- `operator_commands`: copyable CLI command templates for running scheduled
  maintenance and checking the latest pointer with `maintenance-status`.

When `maintenance-status` reads an older latest pointer without command hints, it
generates compatible fallback commands from `--project-root`, `--latest`, and
`--max-age-seconds`.

## Audit Outputs

Run Manifest schema `1.37` records:

- `task_assignments[].claimable`
- `task_assignments[].unmet_dependency_ids`
- `task_assignments[].claimed_at`
- `task_assignments[].claim_token`
- `task_assignments[].claimed_age_seconds`
- `task_assignments[].last_heartbeat_at`
- `task_assignments[].heartbeat_age_seconds`
- `task_assignments[].lease_seconds`
- `task_assignments[].lease_expires_at`
- `task_assignments[].lease_expired`
- `task_assignments[].stale_claimed`
- `task_assignments[].returned_at`
- `task_assignments[].prompt_file`
- `workitems[].remediation_suggestions`
- `workitems[].retry_count`
- `workitems[].max_retries`
- `workitems[].blocked_reason`
- `executions[].remediation_suggestions`
- `executions[].execution_command`
- `executions[].execution_exit_code`
- `scope_contract_results[]`
- `summary.scope_contract_status`
- `summary.scope_contract_violation_count`
- `executions[].execution_duration_ms`
- `executions[].prompt_hash`
- `executions[].token_usage`
- `cli_runs[].prompt_hash`
- `llm_runs[].prompt_hash`
- `llm_runs[].token_usage`
- `llm_runs[].context_length`
- `artifacts[].workitem_id`
- `artifacts[].title`
- `artifacts[].parent_artifact_id`
- `artifacts[].derived_from`
- `artifacts[].review_of`
- `artifacts[].collaboration_session_id`
- `task_prompt_files`
- `files.task_prompts`
- `retry_history[]`
- `summary.task_center_summary`
- `summary.workitem_status_counts`
- `summary.execution_status_counts`
- `summary.failed_workitem_ids`
- `summary.retry_history_count`
- `summary.retry_attempt_count`
- `summary.blocked_reasons`
- `summary.retryable_failure_count`
- `summary.non_retryable_failure_count`
- `summary.cli_run_count`
- `summary.llm_run_count`
- `summary.llm_token_usage`
- `summary.llm_cost_estimate`
- `summary.llm_context_windows`
- `summary.pending_test_scope`
- `resume_cursor.project_status`
- `resume_cursor.current_stage`
- `resume_cursor.next_action`
- `resume_cursor.next_pending_workitem_ids`
- `resume_cursor.running_workitem_ids`
- `resume_cursor.retryable_failed_workitem_ids`
- `resume_cursor.terminal_failed_workitem_ids`
- `resume_cursor.blockers`
- `summary.collaboration_run_count`
- `summary.changed_files`
- `summary.changed_file_count`
- `summary.artifact_file_count`
- `summary.task_prompt_file_count`
- `summary.validation_failure_count`
- `run_environment`
- `platform_diagnostics`
- `executions[].input_artifact_ids`

Project reports include a `## Task Center` section with summary counts, one
line per assignment, and an audit rollup. Audit findings render
`related_assignment_ids`, `related_artifact_ids`, and `missing_artifact_ids`
when present, so repair targets are visible without opening raw JSON.

Use `python -m app.verify_manifest <manifest>` for read-only manifest
self-consistency checks. Use `python -m app.replay_manifest <manifest>` to build
a read-only replay trace from archived Project/WorkItem/Execution/Artifact facts.
Replay traces also render Task Center audit findings and their related/missing
artifact ids.

Board snapshots expose readiness through `BoardTaskAssignmentView.claimable`,
`BoardTaskAssignmentView.unmet_dependency_ids`,
`BoardTaskAssignmentView.claimed_age_seconds`,
`BoardTaskAssignmentView.last_heartbeat_at`,
`BoardTaskAssignmentView.heartbeat_age_seconds`,
`BoardTaskAssignmentView.lease_seconds`,
`BoardTaskAssignmentView.lease_expires_at`,
`BoardTaskAssignmentView.lease_expired`,
`BoardTaskAssignmentView.stale_claimed`, and
`BoardTaskAssignmentView.prompt_file`.
