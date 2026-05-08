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
Use `--prompt-file <path>` to persist the rendered Markdown prompt for audit,
handoff, or direct CLI consumption. Relative paths are resolved under
`project_root`.
Markdown prompts include copyable `complete`, `fail`, and `release` commands
with the current `assignment_id` and `project_root`, so external agents can
return task status without reconstructing the protocol manually.

## Board API

Endpoints:

- `GET /api/projects/{project_id}/tasks`
- `GET /api/projects/{project_id}/tasks?status=queued`
- `GET /api/projects/{project_id}/tasks?stale_only=true&stale_after_seconds=3600`
- `GET /api/projects/{project_id}/tasks/summary`
- `GET /api/projects/{project_id}/tasks/{assignment_id}/context`
- `GET /api/projects/{project_id}/tasks/{assignment_id}/context?format=markdown`
- `POST /api/projects/{project_id}/tasks/claim-next`
- `POST /api/projects/{project_id}/tasks/{assignment_id}/claim`
- `POST /api/projects/{project_id}/tasks/{assignment_id}/complete`
- `POST /api/projects/{project_id}/tasks/{assignment_id}/fail`
- `POST /api/projects/{project_id}/tasks/{assignment_id}/heartbeat`
- `POST /api/projects/{project_id}/tasks/{assignment_id}/release`
- `POST /api/projects/{project_id}/tasks/release-stale`

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
  "prompt_file": ".conductor/task_center/prompts/next-task.md"
}
```

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
  "claim_token": "token-from-claim-response"
}
```

`agent_id` and `claim_token` are optional. When provided, `agent_id` must match
the current claimant and `claim_token` must match the current claim token.

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

`release-stale` bulk releases claimed assignments whose `claimed_at` age is
greater than or equal to the threshold, and returns `released_count`, `summary`,
and the released task payloads.

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
- `assignment`
- `workitem`
- `input_artifacts`
- `output_artifacts`

`input_artifacts[]` includes metadata and, by default, `content` read from the
artifact file path when available. Use CLI `--no-content` or API
`?include_content=false` when only metadata is needed.
`execution_brief` is a compact instruction block for external workers. It
summarizes the project, assignment, acceptance criteria, input artifacts, and
return protocol.
CLI `context --format markdown` renders the same payload as a human-readable
task prompt for coding agents.
API `/context?format=markdown` returns the same prompt as `text/markdown`.

Mutation responses from `claim`, `claim-next`, `complete`, `fail`, `heartbeat`,
and `release` include:

- `ok` for CLI responses
- `project_id`
- `summary`
- `task`

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

## Audit Outputs

Run Manifest schema `1.22` records:

- `task_assignments[].claimable`
- `task_assignments[].unmet_dependency_ids`
- `task_assignments[].claimed_at`
- `task_assignments[].claim_token`
- `task_assignments[].claimed_age_seconds`
- `task_assignments[].last_heartbeat_at`
- `task_assignments[].heartbeat_age_seconds`
- `task_assignments[].stale_claimed`
- `task_assignments[].returned_at`
- `task_assignments[].prompt_file`
- `workitems[].remediation_suggestions`
- `executions[].remediation_suggestions`
- `executions[].execution_command`
- `executions[].execution_exit_code`
- `executions[].execution_duration_ms`
- `artifacts[].workitem_id`
- `artifacts[].title`
- `artifacts[].parent_artifact_id`
- `artifacts[].derived_from`
- `artifacts[].review_of`
- `artifacts[].collaboration_session_id`
- `task_prompt_files`
- `files.task_prompts`
- `summary.task_center_summary`
- `summary.workitem_status_counts`
- `summary.execution_status_counts`
- `summary.failed_workitem_ids`
- `summary.blocked_reasons`
- `summary.retryable_failure_count`
- `summary.non_retryable_failure_count`
- `summary.cli_run_count`
- `summary.llm_run_count`
- `summary.collaboration_run_count`
- `summary.changed_files`
- `summary.changed_file_count`
- `summary.artifact_file_count`
- `summary.task_prompt_file_count`
- `summary.validation_failure_count`
- `run_environment`
- `platform_diagnostics`
- `executions[].input_artifact_ids`

Project reports include a `## Task Center` section with summary counts and one
line per assignment.

Board snapshots expose readiness through `BoardTaskAssignmentView.claimable`,
`BoardTaskAssignmentView.unmet_dependency_ids`,
`BoardTaskAssignmentView.claimed_age_seconds`,
`BoardTaskAssignmentView.last_heartbeat_at`,
`BoardTaskAssignmentView.heartbeat_age_seconds`,
`BoardTaskAssignmentView.stale_claimed`, and
`BoardTaskAssignmentView.prompt_file`.
