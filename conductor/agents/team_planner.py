"""Dynamic Agent team planner."""

from __future__ import annotations

from dataclasses import replace

from conductor.agents.profile import AgentProfile
from conductor.domain.models import (
    AgentTeamPlan,
    Capability,
    DynamicAgentSpec,
    SharedProjectState,
    WorkItem,
)


class AgentTeamPlanner:
    """Plan runtime Agent instances from project state and current work scope."""

    def plan(self, state: SharedProjectState, stage: str | None = None, trigger: str = "stage_start") -> AgentTeamPlan:
        """Return a deterministic dynamic team plan for the selected stage."""
        target_stage = stage or state.current_stage or ""
        workitems = [item for item in state.workitems if item.stage == target_stage]
        features = self._features(state.project.goal, workitems)
        specs: list[DynamicAgentSpec] = []
        reasons: list[str] = []

        if target_stage in {"requirement", "design"}:
            specs.extend(self._planning_specs(target_stage, features, workitems))
        if target_stage == "development":
            specs.extend(self._development_specs(features, workitems))
        if target_stage == "testing":
            specs.extend(self._testing_specs(features, workitems))

        if "api" in features:
            reasons.append("API/backend work detected")
        if "data" in features:
            reasons.append("data/persistence work detected")
        if "validation" in features:
            reasons.append("validation/test risk detected")
        if "security" in features:
            reasons.append("security/access-control risk detected")
        if len(workitems) >= 3:
            reasons.append("multiple WorkItems in current stage")

        specs = self._dedupe_specs(specs)
        complexity_level = self._complexity_level(features, specs, workitems)
        return AgentTeamPlan(
            id=f"team-plan-{target_stage}-{len(state.agent_team_plans) + 1:03d}",
            project_id=state.project.id,
            stage=target_stage,
            trigger=trigger,
            complexity_level=complexity_level,
            reasons=reasons or ["default stage team is sufficient"],
            agent_specs=specs,
            parallel_protocol=self._parallel_protocol(target_stage, specs),
            global_strategy=self._global_strategy(
                target_stage,
                complexity_level=complexity_level,
                features=features,
                workitems=workitems,
                specs=specs,
            ),
        )

    def profile_for_spec(self, base_profile: AgentProfile, spec: DynamicAgentSpec) -> AgentProfile:
        """Build an AgentProfile for a runtime-generated role instance."""
        return replace(
            base_profile,
            mission=spec.mission,
            output_contract=list(spec.output_contract),
            review_focus=list(spec.review_focus),
            revision_rules=list(spec.revision_rules),
            preferred_backend=spec.preferred_backend,
            allowed_collaboration_modes=list(spec.allowed_collaboration_modes),
            default_workitem_kinds=list(spec.workitem_kinds),
        )

    def build_spec(
        self,
        *,
        role: str,
        instance_id: str,
        stage: str,
        mission: str,
        reason: str,
        scope: str,
        mode: str,
        parallel_safe: bool = False,
        write_scope: list[str] | None = None,
        workitem_kinds: list[str] | None = None,
    ) -> DynamicAgentSpec:
        """Build a dynamic Agent spec for controller/TL-owned planning overlays."""
        return self._spec(
            role=role,
            instance_id=instance_id,
            stage=stage,
            mission=mission,
            reason=reason,
            scope=scope,
            mode=mode,
            parallel_safe=parallel_safe,
            write_scope=write_scope,
            workitem_kinds=workitem_kinds,
        )

    def _planning_specs(self, stage: str, features: set[str], workitems: list[WorkItem]) -> list[DynamicAgentSpec]:
        specs: list[DynamicAgentSpec] = []
        if {"data", "export"} & features:
            specs.append(
                self._spec(
                    role="designer",
                    instance_id="information_architecture",
                    stage=stage,
                    mission="Clarify fields, data lifecycle, and import/export boundaries.",
                    reason="Data or export behavior requires a dedicated information architecture perspective.",
                    scope="fields, persistence, filtering, import/export, data boundaries",
                    mode="sequential_review",
                    workitem_kinds=[item.kind for item in workitems],
                )
            )
        if {"workflow", "security"} & features:
            specs.append(
                self._spec(
                    role="solution_designer",
                    instance_id="risk_boundary",
                    stage=stage,
                    mission="Review workflow, permission, and system boundary risks.",
                    reason="Workflow or security terms require solution-level risk review.",
                    scope="actor responsibilities, state transitions, permissions, trust boundaries",
                    mode="sequential_review",
                    workitem_kinds=[item.kind for item in workitems],
                )
            )
        return specs

    def _development_specs(self, features: set[str], workitems: list[WorkItem]) -> list[DynamicAgentSpec]:
        specs: list[DynamicAgentSpec] = []
        kinds = [item.kind for item in workitems]
        if {"api_implementation", "data_implementation"} & set(kinds) or "api" in features or "data" in features:
            specs.extend(
                [
                    self._spec(
                        role="backend_engineer",
                        instance_id="api_contracts",
                        stage="development",
                        mission="Implement API contracts and service boundaries.",
                        reason="Backend work can be split by API contract scope.",
                        scope="routes, request/response contracts, service errors",
                        mode="parallel_development",
                        parallel_safe=True,
                        write_scope=["API route files", "service boundary files"],
                        workitem_kinds=["api_implementation", "generic_implementation"],
                    ),
                    self._spec(
                        role="backend_engineer",
                        instance_id="data_model",
                        stage="development",
                        mission="Implement data model, persistence, and validation boundaries.",
                        reason="Data work can be split from API contract implementation.",
                        scope="schemas, storage adapters, validation helpers, migrations",
                        mode="parallel_development",
                        parallel_safe=True,
                        write_scope=["data model files", "storage layer files", "validation helpers"],
                        workitem_kinds=["data_implementation", "generic_implementation"],
                    ),
                ]
            )
        return specs

    def _testing_specs(self, features: set[str], workitems: list[WorkItem]) -> list[DynamicAgentSpec]:
        specs: list[DynamicAgentSpec] = []
        kinds = [item.kind for item in workitems]
        text = " ".join(item.description for item in workitems).lower()
        review_risk_terms = (
            "edge",
            "error",
            "invalid",
            "regression",
            "api",
            "security",
            "\u8fb9\u754c",
            "\u5f02\u5e38",
            "\u9519\u8bef",
            "\u56de\u5f52",
            "\u754c\u9762",
            "\u63a5\u53e3",
            "\u5b89\u5168",
        )
        complex_testing = (
            ("validation" in features and any(term in text for term in review_risk_terms))
            or any(kind in {"automated_test", "api_validation"} for kind in kinds)
        )
        if complex_testing:
            specs.append(
                self._spec(
                    role="tester",
                    instance_id="acceptance",
                    stage="testing",
                    mission="Validate happy-path acceptance criteria against observable behavior.",
                    reason="Testing stage needs an explicit acceptance owner.",
                    scope="acceptance checklist, release readiness, pass/fail evidence",
                    mode="parallel_review",
                    workitem_kinds=kinds,
                )
            )
            specs.append(
                self._spec(
                    role="tester",
                    instance_id="edge_cases",
                    stage="testing",
                    mission="Validate edge cases, invalid input, and regression risk.",
                    reason="Testing stage needs a separate edge-case reviewer.",
                    scope="negative paths, boundary input, regression risk, failure evidence",
                    mode="parallel_review",
                    workitem_kinds=kinds,
                )
            )
        return specs

    def _spec(
        self,
        *,
        role: str,
        instance_id: str,
        stage: str,
        mission: str,
        reason: str,
        scope: str,
        mode: str,
        parallel_safe: bool = False,
        write_scope: list[str] | None = None,
        workitem_kinds: list[str] | None = None,
    ) -> DynamicAgentSpec:
        stage_part = f"{stage.replace('_', '-')}-" if stage in {"requirement", "design"} else ""
        agent_id = f"agent-{role.replace('_', '-')}-{stage_part}{instance_id.replace('_', '-')}"
        return DynamicAgentSpec(
            role=role,
            agent_id=agent_id,
            instance_id=instance_id,
            stage=stage,
            mission=mission,
            reason=reason,
            scope=scope,
            collaboration_mode=mode,
            parallel_safe=parallel_safe,
            write_scope=write_scope or [],
            output_contract=["scoped deliverable", "risk notes", "handoff evidence"],
            review_focus=[scope],
            revision_rules=["stay within assigned scope", "do not overwrite other agents' write scope"],
            preferred_backend="local",
            allowed_collaboration_modes=[mode, "sequential_review"],
            workitem_kinds=list(dict.fromkeys(workitem_kinds or [])),
        )

    def _features(self, goal: str, workitems: list[WorkItem]) -> set[str]:
        text = " ".join([goal, *[item.description for item in workitems]]).lower()
        rules = {
            "api": ("api", "backend", "endpoint", "接口", "后端", "服务"),
            "data": ("data", "schema", "storage", "database", "csv", "数据", "存储", "持久化", "导出"),
            "workflow": ("workflow", "approve", "reject", "流程", "审批", "流转"),
            "validation": ("test", "validation", "invalid", "error", "测试", "验证", "校验", "错误"),
            "security": ("auth", "permission", "security", "login", "权限", "安全", "登录", "鉴权"),
            "export": ("export", "download", "csv", "导出", "下载"),
        }
        return {feature for feature, terms in rules.items() if any(term in text for term in terms)}

    def _complexity_level(self, features: set[str], specs: list[DynamicAgentSpec], workitems: list[WorkItem]) -> str:
        score = len(features) + len(specs) + (1 if len(workitems) >= 3 else 0)
        if score <= 2:
            return "simple"
        if score <= 5:
            return "standard"
        return "complex"

    def _parallel_protocol(self, stage: str, specs: list[DynamicAgentSpec]) -> dict[str, object]:
        """Return an explicit protocol for safe parallel execution."""
        parallel_specs = [spec for spec in specs if spec.collaboration_mode == "parallel_development"]
        if not parallel_specs:
            return {
                "enabled": False,
                "stage": stage,
                "lanes": [],
                "merge_order": [],
                "shared_contracts": [],
                "integration_owner": "",
                "validation_gates": [],
            }
        lanes = [
            {
                "agent_id": spec.agent_id,
                "role": spec.role,
                "instance_id": spec.instance_id,
                "write_scope": list(spec.write_scope),
                "handoff_required": True,
                "return_contract": list(spec.output_contract),
            }
            for spec in parallel_specs
        ]
        roles = {spec.role for spec in parallel_specs}
        shared_contracts = [
            "Each lane must claim through Task Center before editing.",
            "Each lane must heartbeat or release before another lane may reuse its write scope.",
            "Each lane must return changed files and validation evidence before integration.",
        ]
        return {
            "enabled": True,
            "stage": stage,
            "lanes": lanes,
            "merge_order": [spec.agent_id for spec in parallel_specs],
            "shared_contracts": shared_contracts,
            "integration_owner": self._integration_owner(parallel_specs),
            "validation_gates": [
                "write scopes are disjoint",
                "all claimed lanes returned or released",
                "shared contracts reviewed before final validation",
            ],
        }

    def _integration_owner(self, specs: list[DynamicAgentSpec]) -> str:
        """Select the conservative owner for integration sequencing."""
        for role in ("solution_designer", "backend_engineer"):
            owner = next((spec.agent_id for spec in specs if spec.role == role), "")
            if owner:
                return owner
        return specs[0].agent_id if specs else ""

    def _global_strategy(
        self,
        stage: str,
        *,
        complexity_level: str,
        features: set[str],
        workitems: list[WorkItem],
        specs: list[DynamicAgentSpec],
    ) -> dict[str, object]:
        """Return a planner-level strategy summary for TL review."""
        parallel_count = len([spec for spec in specs if spec.collaboration_mode == "parallel_development"])
        return {
            "stage": stage,
            "posture": "expand_parallel" if parallel_count else "linear_control",
            "complexity_level": complexity_level,
            "primary_risks": sorted(features),
            "recommended_next_action": "claim_parallel_lanes" if parallel_count else "execute_stage_sequence",
            "coordination_policy": (
                "Use Task Center claim/heartbeat/return protocol for every parallel lane."
                if parallel_count
                else "Keep work sequential unless TL adds runtime guard seats."
            ),
            "evidence_gates": [
                "artifact outputs are linked to WorkItems",
                "acceptance criteria remain traceable",
                "Task Center audit is clean before release",
            ],
            "workitem_count": len(workitems),
            "parallel_lane_count": parallel_count,
        }

    def _dedupe_specs(self, specs: list[DynamicAgentSpec]) -> list[DynamicAgentSpec]:
        result: list[DynamicAgentSpec] = []
        seen: set[str] = set()
        for spec in specs:
            if spec.agent_id in seen:
                continue
            seen.add(spec.agent_id)
            result.append(spec)
        return result


__all__ = ["AgentTeamPlanner"]
