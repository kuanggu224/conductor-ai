"""Task Center context payloads for external workers."""

from __future__ import annotations

from dataclasses import asdict

from conductor.artifacts.store import ArtifactStore
from conductor.delivery_contract import build_delivery_contract, render_delivery_contract_markdown
from conductor.domain.models import Artifact, SharedProjectState, TaskAssignment
from conductor.task_center.commands import build_task_return_commands
from conductor.task_center.service import TaskCenterError, TaskCenterService
from conductor.testing.failure_feedback import build_testing_feedback_for_workitem


_REQUIREMENT_BASELINE_KINDS = {"frozen_requirement_spec", "requirement_spec"}
_DESIGN_BASELINE_KINDS = {"frozen_design_spec", "design_overview", "feature_slice_plan", "api_design", "test_design"}
_REQUIREMENT_BASELINE_TERMS = (
    "frozen requirement",
    "requirement baseline",
    "\u51bb\u7ed3\u9700\u6c42",
    "\u9700\u6c42\u57fa\u7ebf",
)
_DESIGN_BASELINE_TERMS = (
    "frozen design",
    "design baseline",
    "design constraint",
    "\u51bb\u7ed3\u8bbe\u8ba1",
    "\u8bbe\u8ba1\u7ea6\u675f",
)


class TaskContextBuilder:
    """Build the readable context an external agent needs for one assignment."""

    def __init__(self, artifact_store: ArtifactStore | None = None) -> None:
        self.artifact_store = artifact_store or ArtifactStore()

    def build(
        self,
        state: SharedProjectState,
        assignment_id: str,
        service: TaskCenterService | None = None,
        *,
        include_content: bool = True,
        max_content_chars: int = 12000,
    ) -> dict[str, object]:
        """Return assignment, WorkItem, and input artifact content."""
        task_center = service or TaskCenterService(_ReadOnlyStateStore(state))
        assignment = task_center.require_assignment(state, assignment_id)
        workitem = next((item for item in state.workitems if item.id == assignment.workitem_id), None)
        if workitem is None:
            raise TaskCenterError(f"WorkItem not found: {assignment.workitem_id}", status_code=404)
        artifacts_by_id = {artifact.id: artifact for artifact in state.artifacts}
        input_artifacts = [
            self._artifact_payload(artifact, include_content=include_content, max_content_chars=max_content_chars)
            for artifact_id in assignment.input_artifact_ids
            if (artifact := artifacts_by_id.get(artifact_id)) is not None
        ]
        self._ensure_frozen_requirement_input(
            state,
            workitem.stage,
            input_artifacts,
            include_content=include_content,
            max_content_chars=max_content_chars,
        )
        self._ensure_frozen_design_input(
            state,
            workitem.stage,
            input_artifacts,
            include_content=include_content,
            max_content_chars=max_content_chars,
        )
        frozen_requirement_baseline = self._frozen_requirement_baseline(input_artifacts)
        frozen_design_baseline = self._frozen_design_baseline(input_artifacts)
        parallel_protocol = self._parallel_protocol_for_assignment(state, assignment)
        eligible_agent_activations = self._eligible_agent_activations(state, assignment, workitem, task_center)
        handoff_safety = self._handoff_safety(
            task_center,
            state,
            assignment,
            workitem,
            input_artifacts,
            eligible_agent_activations,
        )
        rework_context = self._rework_context(state, workitem, input_artifacts)
        delivery_contract = self._delivery_contract(workitem, assignment, input_artifacts, rework_context)
        output_artifacts = [
            self._artifact_payload(artifact, include_content=False, max_content_chars=max_content_chars)
            for artifact in state.artifacts
            if artifact.workitem_id == assignment.workitem_id
        ]
        return {
            "ok": True,
            "project_id": state.project.id,
            "project_goal": state.project.goal,
            "project_root": state.project.project_root,
            "execution_brief": self._execution_brief(
                state,
                assignment,
                input_artifacts,
                frozen_requirement_baseline,
                frozen_design_baseline,
                eligible_agent_activations,
                handoff_safety,
                parallel_protocol,
                rework_context,
                delivery_contract,
            ),
            "frozen_requirement_baseline": frozen_requirement_baseline,
            "frozen_design_baseline": frozen_design_baseline,
            "eligible_agent_activations": eligible_agent_activations,
            "parallel_protocol": parallel_protocol,
            "handoff_safety": handoff_safety,
            "rework_context": rework_context,
            "delivery_contract": delivery_contract,
            "assignment": {
                **asdict(assignment),
                "status": assignment.status.value,
                "claimable": task_center.claimable(state, assignment),
                "unmet_dependency_ids": task_center.unmet_dependency_ids(state, assignment),
                "lease_expired": task_center.lease_expired(assignment),
                "return_commands": build_task_return_commands(state.project.project_root, assignment),
            },
            "workitem": asdict(workitem),
            "input_artifacts": input_artifacts,
            "output_artifacts": output_artifacts,
        }

    def render_markdown(self, payload: dict[str, object]) -> str:
        """Render a context payload as a CLI-agent friendly Markdown prompt."""
        assignment = _dict_payload(payload.get("assignment"))
        workitem = _dict_payload(payload.get("workitem"))
        input_artifacts = _list_payload(payload.get("input_artifacts"))
        output_artifacts = _list_payload(payload.get("output_artifacts"))
        frozen_requirement_baseline = _dict_payload(payload.get("frozen_requirement_baseline"))
        frozen_design_baseline = _dict_payload(payload.get("frozen_design_baseline"))
        eligible_agent_activations = _list_payload(payload.get("eligible_agent_activations"))
        handoff_safety = _dict_payload(payload.get("handoff_safety"))
        rework_context = _dict_payload(payload.get("rework_context"))
        delivery_contract = _dict_payload(payload.get("delivery_contract"))
        acceptance_criteria = _list_payload(workitem.get("acceptance_criteria"))
        testing_checklist = _list_payload(workitem.get("testing_checklist"))

        lines = [
            "# Task Assignment Context",
            "",
            "## Project",
            f"- Project ID: {payload.get('project_id', '')}",
            f"- Goal: {payload.get('project_goal', '')}",
            f"- Root: {payload.get('project_root', '')}",
            "",
            "## Assignment",
            f"- Assignment ID: {assignment.get('id', '')}",
            f"- Status: {assignment.get('status', '')}",
            f"- Role: {assignment.get('role', '')}",
            f"- Assigned Agent: {assignment.get('assigned_agent_id', '') or 'unassigned'}",
            f"- Claim Token: {assignment.get('claim_token', '') or 'not claimed'}",
            f"- Lease Seconds: {assignment.get('lease_seconds', 0) or 0}",
            f"- Lease Expires At: {assignment.get('lease_expires_at', '') or 'not set'}",
            f"- Lease Expired: {assignment.get('lease_expired', False)}",
            f"- Claimable: {assignment.get('claimable', '')}",
            f"- Dependencies: {_join_or_none(_list_payload(assignment.get('dependencies')))}",
            f"- Unmet Dependencies: {_join_or_none(_list_payload(assignment.get('unmet_dependency_ids')))}",
            f"- Prompt File: {assignment.get('prompt_file', '') or 'not recorded'}",
            "",
            "### Eligible Dynamic Agents",
            *self._eligible_agent_markdown(eligible_agent_activations),
            "",
            "## Handoff Safety",
            *self._handoff_safety_markdown(handoff_safety),
            "",
            "## WorkItem",
            f"- WorkItem ID: {workitem.get('id', '')}",
            f"- Stage: {workitem.get('stage', '')}",
            f"- Kind: {workitem.get('kind', '')}",
            f"- Status: {workitem.get('status', '')}",
            "",
            "### Description",
            str(workitem.get("description", "") or "Not specified"),
            "",
            "### Acceptance Criteria",
            *_bullet_lines(acceptance_criteria),
            "",
            "### Testing Checklist",
            *self._testing_checklist_markdown(testing_checklist),
            "",
            "## Delivery Contract",
            *render_delivery_contract_markdown(delivery_contract),
            "",
            "## Frozen Requirement Baseline",
            *self._frozen_requirement_markdown(frozen_requirement_baseline),
            "",
            "## Frozen Design Baseline",
            *self._frozen_design_markdown(frozen_design_baseline),
            "",
            "## Rework Context",
            *self._rework_markdown(rework_context),
            "",
            "## Execution Brief",
            _fenced(str(payload.get("execution_brief", "") or "")),
            "",
            "## Input Artifacts",
        ]
        lines.extend(self._artifact_markdown(input_artifacts, include_content=True))
        lines.extend(
            [
                "",
                "## Existing Output Artifacts",
            ]
        )
        lines.extend(self._artifact_markdown(output_artifacts, include_content=False))
        lines.extend(
            [
                "",
                "## CLI Return Commands",
                *_return_command_lines(payload, assignment),
                "",
                "## Return Protocol",
                "- Return a concise result summary.",
                "- Attach substantive output with `--output-file` or `output_artifact_content`.",
                "- Use fail with a clear blocked reason if the task cannot be completed safely.",
            ]
        )
        return "\n".join(lines).rstrip() + "\n"

    def _artifact_markdown(self, artifacts: list[object], *, include_content: bool) -> list[str]:
        if not artifacts:
            return ["- None"]
        lines: list[str] = []
        for artifact_item in artifacts:
            artifact = _dict_payload(artifact_item)
            lines.extend(
                [
                    f"### {artifact.get('id', '')}",
                    f"- Kind: {artifact.get('kind', '')}",
                    f"- Title: {artifact.get('title', '')}",
                    f"- WorkItem ID: {artifact.get('workitem_id', '')}",
                    f"- Agent ID: {artifact.get('agent_id', '')}",
                    f"- Source Backend: {artifact.get('source_backend', '')}",
                    f"- Path: {artifact.get('path', '')}",
                    f"- Version: {artifact.get('version', '')}",
                    f"- Parent Artifact ID: {artifact.get('parent_artifact_id', '') or '-'}",
                    f"- Review Of: {artifact.get('review_of', '') or '-'}",
                    f"- Derived From: {_join_or_none(_list_payload(artifact.get('derived_from')))}",
                ]
            )
            if include_content and "content" in artifact:
                if artifact.get("content_truncated"):
                    lines.append("- Content: truncated")
                else:
                    lines.append("- Content:")
                lines.append(_fenced(str(artifact.get("content", ""))))
            lines.append("")
        return lines[:-1] if lines and lines[-1] == "" else lines

    def _frozen_requirement_baseline(self, input_artifacts: list[dict[str, object]]) -> dict[str, object]:
        """Return the frozen requirement artifact that controls downstream work."""
        for artifact in input_artifacts:
            if artifact.get("kind") == "frozen_requirement_spec":
                return artifact
        return {}

    def _frozen_design_baseline(self, input_artifacts: list[dict[str, object]]) -> dict[str, object]:
        """Return the frozen design artifact that controls implementation and testing."""
        for artifact in input_artifacts:
            if artifact.get("kind") == "frozen_design_spec":
                return artifact
        return {}

    def _eligible_agent_activations(
        self,
        state: SharedProjectState,
        assignment: TaskAssignment,
        workitem: object,
        task_center: TaskCenterService,
    ) -> list[dict[str, object]]:
        """Return dynamic Agent instances that are suitable for this assignment."""
        workitem_payload = asdict(workitem)
        workitem_kind = str(workitem_payload.get("kind") or "")
        workitem_stage = str(workitem_payload.get("stage") or "")
        matches: list[dict[str, object]] = []
        for activation in state.agent_activations:
            if activation.role != assignment.role:
                continue
            if activation.stage and activation.stage != workitem_stage:
                continue
            if activation.related_workitem_kinds and workitem_kind not in activation.related_workitem_kinds:
                continue
            write_scope_conflicts = task_center.write_scope_conflicts(
                state,
                assignment,
                agent_id=activation.agent_id,
            )
            matches.append(
                {
                    "agent_id": activation.agent_id,
                    "role": activation.role,
                    "instance_id": activation.instance_id,
                    "reason": activation.reason,
                    "scope": activation.scope,
                    "parallel_safe": activation.parallel_safe,
                    "write_scope": list(activation.write_scope),
                    "parallel_lane": self._parallel_lane_for_agent(state, activation.agent_id),
                    "parallel_protocol": self._parallel_summary_for_agent(state, activation.agent_id),
                    "preferred_backend": activation.preferred_backend,
                    "execution_backend": activation.execution_backend,
                    "claimable_for_agent": task_center.claimable(
                        state,
                        assignment,
                        agent_id=activation.agent_id,
                    ),
                    "write_scope_conflict_assignment_ids": write_scope_conflicts,
                }
            )
        return matches

    def _parallel_protocol_for_assignment(
        self,
        state: SharedProjectState,
        assignment: TaskAssignment,
    ) -> dict[str, object]:
        """Return the current-stage parallel protocol relevant to this assignment."""
        plan = self._latest_team_plan_for_stage(state, state.current_stage or "")
        protocol = dict(getattr(plan, "parallel_protocol", {}) if plan else {})
        if not protocol:
            return {"enabled": False, "lanes": [], "merge_order": [], "shared_contracts": [], "validation_gates": []}
        matching_lanes = [
            lane
            for lane in _list_payload(protocol.get("lanes"))
            if isinstance(lane, dict)
            and (
                str(lane.get("role", "")) == assignment.role
                or str(lane.get("agent_id", "")) == (assignment.assigned_agent_id or "")
            )
        ]
        return {
            "enabled": bool(protocol.get("enabled", False)),
            "stage": str(protocol.get("stage", "")),
            "integration_owner": str(protocol.get("integration_owner", "")),
            "merge_order": _list_payload(protocol.get("merge_order")),
            "shared_contracts": _list_payload(protocol.get("shared_contracts")),
            "validation_gates": _list_payload(protocol.get("validation_gates")),
            "matching_lanes": matching_lanes,
        }

    def _parallel_lane_for_agent(self, state: SharedProjectState, agent_id: str) -> dict[str, object]:
        """Return the declared parallel lane for one dynamic Agent, if any."""
        summary = self._parallel_summary_for_agent(state, agent_id)
        return _dict_payload(summary.get("lane"))

    def _parallel_summary_for_agent(self, state: SharedProjectState, agent_id: str) -> dict[str, object]:
        plan = self._latest_team_plan_for_stage(state, state.current_stage or "")
        protocol = dict(getattr(plan, "parallel_protocol", {}) if plan else {})
        if not protocol:
            return {"enabled": False, "lane": {}, "merge_order_index": -1, "integration_owner": ""}
        lane = next(
            (
                _dict_payload(item)
                for item in _list_payload(protocol.get("lanes"))
                if isinstance(item, dict) and str(item.get("agent_id", "")) == agent_id
            ),
            {},
        )
        merge_order = [str(item) for item in _list_payload(protocol.get("merge_order"))]
        return {
            "enabled": bool(protocol.get("enabled", False)),
            "lane": lane,
            "merge_order_index": merge_order.index(agent_id) if agent_id in merge_order else -1,
            "integration_owner": str(protocol.get("integration_owner", "")),
            "shared_contracts": _list_payload(protocol.get("shared_contracts")),
            "validation_gates": _list_payload(protocol.get("validation_gates")),
        }

    def _latest_team_plan_for_stage(self, state: SharedProjectState, stage: str):
        for plan in reversed(state.agent_team_plans):
            if plan.stage == stage:
                return plan
        return None

    def _handoff_safety(
        self,
        task_center: TaskCenterService,
        state: SharedProjectState,
        assignment: TaskAssignment,
        workitem: object,
        input_artifacts: list[dict[str, object]],
        eligible_agent_activations: list[dict[str, object]],
    ) -> dict[str, object]:
        """Return a compact safety summary for external Agent handoff."""
        unmet_dependency_ids = task_center.unmet_dependency_ids(state, assignment)
        assignment_conflicts = task_center.write_scope_conflicts(state, assignment)
        baseline_handoff = self._development_baseline_handoff(state, workitem, input_artifacts)
        activation_conflicts = [
            str(conflict_id)
            for activation in eligible_agent_activations
            for conflict_id in _list_payload(activation.get("write_scope_conflict_assignment_ids"))
        ]
        write_scope_conflicts = list(dict.fromkeys([*assignment_conflicts, *activation_conflicts]))
        blocked_agent_count = len(
            [activation for activation in eligible_agent_activations if not activation.get("claimable_for_agent", False)]
        )
        assignment_claimable = task_center.claimable(state, assignment)
        has_claimable_dynamic_agent = any(
            bool(activation.get("claimable_for_agent", False)) for activation in eligible_agent_activations
        )
        ready_for_handoff = assignment_claimable and (
            not eligible_agent_activations or has_claimable_dynamic_agent
        )
        warnings: list[str] = []
        if unmet_dependency_ids:
            warnings.append("assignment dependencies are not satisfied")
        if write_scope_conflicts:
            warnings.append("write scope conflicts with claimed assignments")
        if eligible_agent_activations and not has_claimable_dynamic_agent:
            warnings.append("no eligible dynamic agent can safely claim this assignment now")
        warnings.extend(str(warning) for warning in _list_payload(baseline_handoff.get("warnings")))
        return {
            "ready_for_handoff": ready_for_handoff,
            "status": "ready" if ready_for_handoff else "blocked",
            "assignment_claimable": assignment_claimable,
            "eligible_agent_count": len(eligible_agent_activations),
            "blocked_agent_count": blocked_agent_count,
            "unmet_dependency_ids": unmet_dependency_ids,
            "write_scope_conflict_assignment_ids": write_scope_conflicts,
            "baseline_handoff": baseline_handoff,
            "warnings": warnings,
            "guidance": self._handoff_guidance(
                ready_for_handoff=ready_for_handoff,
                unmet_dependency_ids=unmet_dependency_ids,
                write_scope_conflicts=write_scope_conflicts,
                eligible_agent_activations=eligible_agent_activations,
            ),
        }

    def _development_baseline_handoff(
        self,
        state: SharedProjectState,
        workitem: object,
        input_artifacts: list[dict[str, object]],
    ) -> dict[str, object]:
        """Return whether development acceptance criteria preserve baseline inputs."""
        workitem_payload = asdict(workitem)
        if str(workitem_payload.get("stage") or "") != "development":
            return {
                "status": "not_applicable",
                "requirement_baseline_artifact_ids": [],
                "design_baseline_artifact_ids": [],
                "missing_contracts": [],
                "warnings": [],
            }
        design_workitem_ids = {item.id for item in state.workitems if item.stage == "design"}
        requirement_baseline_ids = [
            str(artifact.get("id", ""))
            for artifact in input_artifacts
            if str(artifact.get("kind", "")) in _REQUIREMENT_BASELINE_KINDS
        ]
        design_baseline_ids = [
            str(artifact.get("id", ""))
            for artifact in input_artifacts
            if str(artifact.get("kind", "")) in _DESIGN_BASELINE_KINDS
            or str(artifact.get("workitem_id", "")) in design_workitem_ids
        ]
        criteria_text = "\n".join(
            str(item) for item in _list_payload(workitem_payload.get("acceptance_criteria"))
        ).lower()
        missing_contracts: list[str] = []
        warnings: list[str] = []
        if requirement_baseline_ids and not _contains_any(criteria_text, _REQUIREMENT_BASELINE_TERMS):
            missing_contracts.append("requirement_baseline")
            warnings.append("development acceptance criteria do not explicitly preserve the requirement baseline")
        if design_baseline_ids and not _contains_any(criteria_text, _DESIGN_BASELINE_TERMS):
            missing_contracts.append("design_baseline")
            warnings.append("development acceptance criteria do not explicitly preserve the design baseline")
        has_baseline_inputs = bool(requirement_baseline_ids or design_baseline_ids)
        return {
            "status": "needs_attention" if missing_contracts else ("covered" if has_baseline_inputs else "not_applicable"),
            "requirement_baseline_artifact_ids": requirement_baseline_ids,
            "design_baseline_artifact_ids": design_baseline_ids,
            "missing_contracts": missing_contracts,
            "warnings": warnings,
        }

    def _rework_context(
        self,
        state: SharedProjectState,
        workitem: object,
        input_artifacts: list[dict[str, object]],
    ) -> dict[str, object]:
        """Return explicit rework metadata for agents handling feedback loops."""
        workitem_payload = asdict(workitem)
        feedback_from = _list_payload(workitem_payload.get("feedback_from"))
        rework_of = str(workitem_payload.get("rework_of") or "")
        feedback_artifacts = [
            {
                "id": artifact.get("id", ""),
                "kind": artifact.get("kind", ""),
                "title": artifact.get("title", ""),
                "workitem_id": artifact.get("workitem_id", ""),
            }
            for artifact in input_artifacts
            if artifact.get("workitem_id") in feedback_from
        ]
        original_artifacts = [
            {
                "id": artifact.get("id", ""),
                "kind": artifact.get("kind", ""),
                "title": artifact.get("title", ""),
                "workitem_id": artifact.get("workitem_id", ""),
            }
            for artifact in input_artifacts
            if rework_of and artifact.get("workitem_id") == rework_of
        ]
        testing_feedback = self._testing_feedback_payloads(state, feedback_from)
        pending_retest_scope = self._pending_retest_scope(state, feedback_from)
        return {
            "is_rework": bool(feedback_from or rework_of),
            "feedback_from": feedback_from,
            "rework_of": rework_of,
            "feedback_artifacts": feedback_artifacts,
            "original_artifacts": original_artifacts,
            "pending_retest_scope": pending_retest_scope,
            "testing_feedback": testing_feedback,
        }

    def _pending_retest_scope(self, state: SharedProjectState, feedback_from: list[str]) -> list[str]:
        """Return the smallest testing WorkItem kinds that should be rerun after this rework."""
        by_id = {item.id: item for item in state.workitems}
        scope = [
            item.kind
            for workitem_id in feedback_from
            if (item := by_id.get(workitem_id)) is not None and item.stage == "testing" and item.kind
        ]
        if not scope:
            scope = list(state.pending_test_scope)
        return list(dict.fromkeys(scope))

    def _testing_feedback_payloads(self, state: SharedProjectState, feedback_from: list[str]) -> list[dict[str, object]]:
        """Return machine-readable structured feedback for failed testing WorkItems."""
        payloads: list[dict[str, object]] = []
        for item in state.workitems:
            if item.id not in feedback_from or item.stage != "testing":
                continue
            payloads.extend(asdict(feedback) for feedback in build_testing_feedback_for_workitem(state, item))
        return payloads

    def _testing_checklist_markdown(self, checklist: list[object]) -> list[str]:
        """Render machine-readable testing checklist entries for tester agents."""
        if not checklist:
            return ["- None"]
        lines: list[str] = []
        for item in checklist:
            if not isinstance(item, dict):
                continue
            evidence_terms = _join_or_none(_list_payload(item.get("required_evidence_terms")))
            requirement_terms = _join_or_none(_list_payload(item.get("requirement_terms")))
            lines.append(
                f"- {item.get('rule_id', '')} | {item.get('label', '')} | "
                f"status={item.get('status', '')} | requirement_terms={requirement_terms} | "
                f"required_evidence={evidence_terms}"
            )
        return lines or ["- None"]

    def _delivery_contract(
        self,
        workitem: object,
        assignment: TaskAssignment,
        input_artifacts: list[dict[str, object]],
        rework_context: dict[str, object],
    ) -> dict[str, object]:
        """Return the explicit stage-level contract an external Agent must satisfy."""
        workitem_payload = asdict(workitem)
        stage = str(workitem_payload.get("stage") or "")
        kind = str(workitem_payload.get("kind") or "")
        artifact_kinds = [str(artifact.get("kind", "")) for artifact in input_artifacts if artifact.get("kind")]
        input_ids = [str(artifact.get("id", "")) for artifact in input_artifacts if artifact.get("id")]
        return build_delivery_contract(
            stage=stage,
            kind=kind,
            role=assignment.role,
            required_input_artifact_ids=input_ids,
            required_input_kinds=artifact_kinds,
            is_rework=bool(rework_context.get("is_rework")),
        )

    def _ensure_frozen_requirement_input(
        self,
        state: SharedProjectState,
        stage: str,
        input_artifacts: list[dict[str, object]],
        *,
        include_content: bool,
        max_content_chars: int,
    ) -> None:
        """Downstream task contexts must carry the latest frozen requirement."""
        if stage not in {"design", "development", "testing"}:
            return
        if any(artifact.get("kind") == "frozen_requirement_spec" for artifact in input_artifacts):
            return
        frozen_requirement = next(
            (artifact for artifact in reversed(state.artifacts) if artifact.kind == "frozen_requirement_spec"),
            None,
        )
        if frozen_requirement is not None:
            input_artifacts.insert(
                0,
                self._artifact_payload(
                    frozen_requirement,
                    include_content=include_content,
                    max_content_chars=max_content_chars,
                ),
            )

    def _ensure_frozen_design_input(
        self,
        state: SharedProjectState,
        stage: str,
        input_artifacts: list[dict[str, object]],
        *,
        include_content: bool,
        max_content_chars: int,
    ) -> None:
        """Development and testing task contexts must carry the latest frozen design."""
        if stage not in {"development", "testing"}:
            return
        if any(artifact.get("kind") == "frozen_design_spec" for artifact in input_artifacts):
            return
        frozen_design = next(
            (artifact for artifact in reversed(state.artifacts) if artifact.kind == "frozen_design_spec"),
            None,
        )
        if frozen_design is None:
            return
        insert_at = 1 if input_artifacts and input_artifacts[0].get("kind") == "frozen_requirement_spec" else 0
        input_artifacts.insert(
            insert_at,
            self._artifact_payload(
                frozen_design,
                include_content=include_content,
                max_content_chars=max_content_chars,
            ),
        )

    def _frozen_requirement_markdown(self, baseline: dict[str, object]) -> list[str]:
        """Render the controlling requirement contract section."""
        if not baseline:
            return ["- None"]
        return [
            "- Treat this frozen requirement as the controlling contract.",
            "- Do not add features outside this baseline unless the task explicitly asks for requirement rework.",
            f"- Artifact ID: {baseline.get('id', '')}",
            f"- Title: {baseline.get('title', '')}",
            f"- Path: {baseline.get('path', '')}",
            "- Full content is included again under `Input Artifacts`.",
        ]

    def _frozen_design_markdown(self, baseline: dict[str, object]) -> list[str]:
        """Render the controlling design baseline section."""
        if not baseline:
            return ["- None"]
        return [
            "- Treat this frozen design as the controlling implementation and testing baseline.",
            "- Do not change architecture, API shape, or UI behavior unless the task explicitly asks for design rework.",
            f"- Artifact ID: {baseline.get('id', '')}",
            f"- Title: {baseline.get('title', '')}",
            f"- Path: {baseline.get('path', '')}",
            "- Full content is included again under `Input Artifacts`.",
        ]

    def _eligible_agent_markdown(self, activations: list[object]) -> list[str]:
        """Render dynamic Agent candidates for this assignment."""
        if not activations:
            return ["- None"]
        lines: list[str] = []
        for item in activations:
            activation = _dict_payload(item)
            write_scope = _join_or_none(_list_payload(activation.get("write_scope")))
            conflict_ids = _join_or_none(_list_payload(activation.get("write_scope_conflict_assignment_ids")))
            lines.append(
                f"- {activation.get('agent_id', '')} ({activation.get('instance_id', '')}) | "
                f"parallel_safe={activation.get('parallel_safe', False)} | "
                f"claimable_for_agent={activation.get('claimable_for_agent', False)} | "
                f"scope={activation.get('scope', '')} | write_scope={write_scope} | "
                f"write_scope_conflicts={conflict_ids}"
            )
        return lines

    def _handoff_safety_markdown(self, safety: dict[str, object]) -> list[str]:
        """Render external worker handoff readiness."""
        baseline_handoff = _dict_payload(safety.get("baseline_handoff"))
        return [
            f"- Status: {safety.get('status', 'unknown')}",
            f"- Ready For Handoff: {safety.get('ready_for_handoff', False)}",
            f"- Assignment Claimable: {safety.get('assignment_claimable', False)}",
            f"- Eligible Dynamic Agents: {safety.get('eligible_agent_count', 0)}",
            f"- Blocked Dynamic Agents: {safety.get('blocked_agent_count', 0)}",
            f"- Unmet Dependencies: {_join_or_none(_list_payload(safety.get('unmet_dependency_ids')))}",
            (
                "- Write Scope Conflicts: "
                f"{_join_or_none(_list_payload(safety.get('write_scope_conflict_assignment_ids')))}"
            ),
            f"- Baseline Contract: {baseline_handoff.get('status', 'not_applicable')}",
            (
                "- Requirement Baseline Artifacts: "
                f"{_join_or_none(_list_payload(baseline_handoff.get('requirement_baseline_artifact_ids')))}"
            ),
            (
                "- Design Baseline Artifacts: "
                f"{_join_or_none(_list_payload(baseline_handoff.get('design_baseline_artifact_ids')))}"
            ),
            (
                "- Missing Baseline Contracts: "
                f"{_join_or_none(_list_payload(baseline_handoff.get('missing_contracts')))}"
            ),
            f"- Warnings: {_join_or_none(_list_payload(safety.get('warnings')))}",
            f"- Guidance: {safety.get('guidance', '')}",
        ]

    def _parallel_protocol_markdown(self, protocol: dict[str, object]) -> list[str]:
        """Render the parallel development protocol for external workers."""
        if not protocol.get("enabled", False):
            return ["- Parallel Protocol: disabled"]
        lanes = []
        for lane in _list_payload(protocol.get("matching_lanes")):
            lane_payload = _dict_payload(lane)
            lanes.append(
                f"{lane_payload.get('agent_id', '')}({lane_payload.get('instance_id', '')}) "
                f"scope={_join_or_none(_list_payload(lane_payload.get('write_scope')))}"
            )
        return [
            "- Parallel Protocol: enabled",
            f"- Integration Owner: {protocol.get('integration_owner', '')}",
            f"- Merge Order: {_join_or_none(_list_payload(protocol.get('merge_order')))}",
            f"- Matching Lanes: {_join_or_none(lanes)}",
            f"- Shared Contracts: {_join_or_none(_list_payload(protocol.get('shared_contracts')))}",
            f"- Validation Gates: {_join_or_none(_list_payload(protocol.get('validation_gates')))}",
        ]

    def _handoff_guidance(
        self,
        *,
        ready_for_handoff: bool,
        unmet_dependency_ids: list[str],
        write_scope_conflicts: list[str],
        eligible_agent_activations: list[dict[str, object]],
    ) -> str:
        if ready_for_handoff:
            return "Assignment is safe to claim with the matching role or a claimable dynamic Agent."
        if unmet_dependency_ids:
            return "Wait for unmet dependencies to complete before claiming this assignment."
        if write_scope_conflicts:
            return "Wait for conflicting claimed assignments to return or release before modifying files."
        if eligible_agent_activations:
            return "No listed dynamic Agent can safely claim this assignment right now."
        return "Assignment is not currently claimable."

    def _rework_markdown(self, context: dict[str, object]) -> list[str]:
        """Render feedback-loop guidance when the task is a rework item."""
        if not context.get("is_rework"):
            return ["- None"]
        feedback_artifacts = _list_payload(context.get("feedback_artifacts"))
        artifact_lines = [
            f"- {artifact.get('id', '')} ({artifact.get('kind', '')}) from {artifact.get('workitem_id', '')}"
            for artifact in feedback_artifacts
            if isinstance(artifact, dict)
        ]
        original_artifacts = _list_payload(context.get("original_artifacts"))
        original_artifact_lines = [
            f"- {artifact.get('id', '')} ({artifact.get('kind', '')}) from {artifact.get('workitem_id', '')}"
            for artifact in original_artifacts
            if isinstance(artifact, dict)
        ]
        testing_feedback_lines = self._testing_feedback_markdown(_list_payload(context.get("testing_feedback")))
        return [
            "- This is a rework task. Preserve the frozen requirement scope and fix only the referenced feedback.",
            f"- Rework Of: {context.get('rework_of', '') or '-'}",
            f"- Feedback From: {_join_or_none(_list_payload(context.get('feedback_from')))}",
            f"- Pending Retest Scope: {_join_or_none(_list_payload(context.get('pending_retest_scope')))}",
            "- Feedback Artifacts:",
            *(artifact_lines or ["- None"]),
            "- Original Artifacts:",
            *(original_artifact_lines or ["- None"]),
            "- Structured Testing Feedback:",
            *(testing_feedback_lines or ["- None"]),
        ]

    def _testing_feedback_markdown(self, feedback_payloads: list[object]) -> list[str]:
        """Render structured testing feedback compactly for CLI prompts."""
        lines: list[str] = []
        for payload in feedback_payloads:
            if not isinstance(payload, dict):
                continue
            failing_checks = _join_or_none(_list_payload(payload.get("failing_checks")))
            missing_coverage = _join_or_none(_list_payload(payload.get("missing_coverage")))
            missing_checklist = _join_or_none(
                [
                    self._missing_checklist_brief(item)
                    for item in _list_payload(payload.get("missing_checklist_items"))
                    if isinstance(item, dict)
                ]
            )
            suggestions = _join_or_none(_list_payload(payload.get("suggested_actions")))
            validation_command = _join_or_none(_list_payload(payload.get("validation_command")))
            lines.extend(
                [
                    (
                        f"- {payload.get('workitem_id', '')}: "
                        f"type={payload.get('failure_type', '') or 'unknown'}, "
                        f"exit_code={payload.get('exit_code', '') or '-'}, "
                        f"validation_exit_code={payload.get('validation_exit_code', '') or '-'}, "
                        f"summary={payload.get('summary', '') or '-'}"
                    ),
                    f"- Validation Command: {validation_command}",
                    f"- Failing Checks: {failing_checks}",
                    f"- Missing Coverage: {missing_coverage}",
                    f"- Missing Checklist Items: {missing_checklist}",
                    f"- Suggested Fixes: {suggestions}",
                ]
            )
        return lines

    def _missing_checklist_brief(self, item: dict[str, object]) -> str:
        """Render a missing checklist item with required evidence for rework prompts."""
        rule_id = str(item.get("rule_id", "")).strip()
        label = str(item.get("label", "")).strip()
        evidence_terms = [
            str(value).strip()
            for value in _list_payload(item.get("required_evidence_terms"))
            if str(value).strip()
        ]
        identity = " ".join(value for value in (rule_id, label) if value) or "-"
        evidence = ", ".join(evidence_terms) if evidence_terms else "-"
        return f"{identity} -> {evidence}"

    def _execution_brief(
        self,
        state: SharedProjectState,
        assignment: TaskAssignment,
        input_artifacts: list[dict[str, object]],
        frozen_requirement_baseline: dict[str, object],
        frozen_design_baseline: dict[str, object],
        eligible_agent_activations: list[dict[str, object]],
        handoff_safety: dict[str, object],
        parallel_protocol: dict[str, object],
        rework_context: dict[str, object],
        delivery_contract: dict[str, object],
    ) -> str:
        criteria = "\n".join(f"- {item}" for item in self._workitem_criteria(state, assignment)) or "- Not specified"
        inputs = "\n".join(f"- {item['id']} ({item['kind']}): {item['title']}" for item in input_artifacts) or "- None"
        frozen_requirement = self._frozen_requirement_brief(frozen_requirement_baseline)
        frozen_design = self._frozen_design_brief(frozen_design_baseline)
        eligible_agents = "\n".join(self._eligible_agent_markdown(eligible_agent_activations))
        handoff_brief = "\n".join(self._handoff_safety_markdown(handoff_safety))
        parallel_brief = "\n".join(self._parallel_protocol_markdown(parallel_protocol))
        rework_brief = "\n".join(self._rework_markdown(rework_context))
        contract_brief = "\n".join(render_delivery_contract_markdown(delivery_contract))
        return (
            f"Project: {state.project.goal}\n"
            f"TaskAssignment: {assignment.id}\n"
            f"WorkItem: {assignment.workitem_id}\n"
            f"Role: {assignment.role}\n\n"
            "Eligible Dynamic Agents:\n"
            f"{eligible_agents}\n\n"
            "Handoff Safety:\n"
            f"{handoff_brief}\n\n"
            "Parallel Development Protocol:\n"
            f"{parallel_brief}\n\n"
            "Delivery Contract:\n"
            f"{contract_brief}\n\n"
            "Frozen Requirement Baseline:\n"
            f"{frozen_requirement}\n\n"
            "Frozen Design Baseline:\n"
            f"{frozen_design}\n\n"
            "Rework Context:\n"
            f"{rework_brief}\n\n"
            "Acceptance Criteria:\n"
            f"{criteria}\n\n"
            "Input Artifacts To Read:\n"
            f"{inputs}\n\n"
            "Return Protocol:\n"
            "- Complete with a concise result_summary.\n"
            "- Attach output_artifact_content or --output-file when returning substantive work.\n"
            "- Use fail/blocked_reason if the task cannot be completed safely."
        )

    def _frozen_requirement_brief(self, baseline: dict[str, object]) -> str:
        """Return a compact baseline instruction for the execution brief."""
        if not baseline:
            return "- None"
        return (
            f"- {baseline.get('id', '')} ({baseline.get('title', '')})\n"
            "- This is the controlling contract for design, implementation, and testing.\n"
            "- Do not expand non-goals or introduce unrelated features."
        )

    def _frozen_design_brief(self, baseline: dict[str, object]) -> str:
        """Return a compact design baseline instruction for the execution brief."""
        if not baseline:
            return "- None"
        return (
            f"- {baseline.get('id', '')} ({baseline.get('title', '')})\n"
            "- This is the controlling baseline for implementation and testing.\n"
            "- Preserve agreed architecture, API shape, UI behavior, and validation boundaries."
        )

    def _workitem_criteria(self, state: SharedProjectState, assignment: TaskAssignment) -> list[str]:
        workitem = next((item for item in state.workitems if item.id == assignment.workitem_id), None)
        return list(workitem.acceptance_criteria) if workitem else []

    def _artifact_payload(
        self,
        artifact: Artifact,
        *,
        include_content: bool,
        max_content_chars: int,
    ) -> dict[str, object]:
        payload = {
            "id": artifact.id,
            "kind": artifact.kind,
            "title": artifact.title,
            "workitem_id": artifact.workitem_id,
            "agent_id": artifact.agent_id,
            "source_backend": artifact.source_backend,
            "path": artifact.path or "",
            "version": artifact.version,
            "parent_artifact_id": artifact.parent_artifact_id or "",
            "derived_from": list(artifact.derived_from),
            "review_of": artifact.review_of or "",
        }
        if include_content:
            content = self.artifact_store.read_content(artifact)
            truncated = len(content) > max_content_chars
            payload["content"] = content[:max_content_chars].rstrip() if truncated else content
            payload["content_truncated"] = truncated
        return payload


class _ReadOnlyStateStore:
    """Minimal state adapter for TaskCenterService helper methods."""

    def __init__(self, state: SharedProjectState) -> None:
        self.state = state

    def get_state(self, project_id: str) -> SharedProjectState:
        if self.state.project.id != project_id:
            raise KeyError(project_id)
        return self.state


def _dict_payload(value: object) -> dict[str, object]:
    return value if isinstance(value, dict) else {}


def _list_payload(value: object) -> list[object]:
    return value if isinstance(value, list) else []


def _bullet_lines(items: list[object]) -> list[str]:
    return [f"- {item}" for item in items] if items else ["- Not specified"]


def _join_or_none(items: list[object]) -> str:
    return ", ".join(str(item) for item in items) if items else "None"


def _contains_any(text: str, terms: tuple[str, ...]) -> bool:
    return any(term.lower() in text for term in terms)


def _fenced(content: str) -> str:
    return f"````text\n{content.rstrip()}\n````"


def _return_command_lines(payload: dict[str, object], assignment: dict[str, object]) -> list[str]:
    return_commands = _dict_payload(assignment.get("return_commands"))
    project_root = str(payload.get("project_root", "") or ".")
    assignment_id = str(assignment.get("id", "") or "<assignment-id>")
    agent_id = str(assignment.get("assigned_agent_id", "") or "<agent-id>")
    claim_token = str(assignment.get("claim_token", "") or "<claim-token>")
    lease_seconds = int(assignment.get("lease_seconds", 0) or 0)
    lease_arg = f" --lease-seconds {lease_seconds}" if lease_seconds > 0 else ""
    complete_command = str(return_commands.get("complete_with_output_file") or "") or (
        f'python -m app.task_center complete "{assignment_id}" '
        f'--project-root "{project_root}" '
        f'--agent-id "{agent_id}" '
        f'--claim-token "{claim_token}" '
        '--result-summary "completed" '
        '--output-file result.md'
    )
    fail_command = str(return_commands.get("fail") or "") or (
        f'python -m app.task_center fail "{assignment_id}" '
        f'--project-root "{project_root}" '
        f'--agent-id "{agent_id}" '
        f'--claim-token "{claim_token}" '
        '--result-summary "failed" '
        '--blocked-reason "explain blocker"'
    )
    heartbeat_command = str(return_commands.get("heartbeat") or "") or (
        f'python -m app.task_center heartbeat "{assignment_id}" '
        f'--project-root "{project_root}" '
        f'--agent-id "{agent_id}" '
        f'--claim-token "{claim_token}"'
        f"{lease_arg}"
    )
    release_command = str(return_commands.get("release") or "") or (
        f'python -m app.task_center release "{assignment_id}" '
        f'--project-root "{project_root}" '
        f'--agent-id "{agent_id}" '
        f'--claim-token "{claim_token}" '
        '--release-reason "worker interrupted"'
    )
    return [
        "Use one of these commands after finishing the task:",
        "",
        "Complete successfully:",
        _fenced(complete_command),
        "",
        "Return failure/blocker:",
        _fenced(fail_command),
        "",
        "Refresh heartbeat while still working:",
        _fenced(heartbeat_command),
        "",
        "Release for retry:",
        _fenced(release_command),
    ]


__all__ = ["TaskContextBuilder"]
