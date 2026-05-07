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

Claim rules:

- `claim` and `claim-next` only allow `queued` assignments.
- Direct `claim` also validates dependency readiness.
- `claim-next` skips assignments with unmet dependencies.
- Failed dependency checks report `unmet_dependency_ids`.

## CLI

Use the persisted project root:

```bash
python -m app.task_center summary --project-root <project-root>
python -m app.task_center list --project-root <project-root>
python -m app.task_center list --project-root <project-root> --status queued
python -m app.task_center claim-next --project-root <project-root> --agent-id <agent-id> --role backend_engineer
python -m app.task_center claim <assignment-id> --project-root <project-root> --agent-id <agent-id>
python -m app.task_center complete <assignment-id> --project-root <project-root> --result-summary "done"
python -m app.task_center fail <assignment-id> --project-root <project-root> --blocked-reason "reason"
```

Use `--state-dir` when the state directory is not under
`<project-root>/.conductor/state`.

## Board API

Endpoints:

- `GET /api/projects/{project_id}/tasks`
- `GET /api/projects/{project_id}/tasks?status=queued`
- `GET /api/projects/{project_id}/tasks/summary`
- `POST /api/projects/{project_id}/tasks/claim-next`
- `POST /api/projects/{project_id}/tasks/{assignment_id}/claim`
- `POST /api/projects/{project_id}/tasks/{assignment_id}/complete`
- `POST /api/projects/{project_id}/tasks/{assignment_id}/fail`

`claim-next` request body:

```json
{
  "agent_id": "agent-backend",
  "role": "backend_engineer",
  "claim_reason": "external worker"
}
```

`complete` request body:

```json
{
  "result_summary": "implemented",
  "output_artifact_ids": ["artifact-1"]
}
```

`fail` request body:

```json
{
  "result_summary": "validation failed",
  "blocked_reason": "missing dependency",
  "output_artifact_ids": []
}
```

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

Mutation responses from `claim`, `claim-next`, `complete`, and `fail` include:

- `ok` for CLI responses
- `project_id`
- `summary`
- `task`

The mutation `summary` is computed after the state transition. Clients can use
it to update dashboards without issuing a second summary request.

Task payloads include:

- `id`
- `workitem_id`
- `role`
- `status`
- `assigned_agent_id`
- `claim_reason`
- `claimable`
- `unmet_dependency_ids`
- `dependencies`
- `input_artifact_ids`
- `output_artifact_ids`
- `result_summary`
- `blocked_reason`
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

## Audit Outputs

Run Manifest schema `1.8` records:

- `task_assignments[].claimable`
- `task_assignments[].unmet_dependency_ids`
- `summary.task_center_summary`

Project reports include a `## Task Center` section with summary counts and one
line per assignment.

Board snapshots expose readiness through `BoardTaskAssignmentView.claimable`
and `BoardTaskAssignmentView.unmet_dependency_ids`.
