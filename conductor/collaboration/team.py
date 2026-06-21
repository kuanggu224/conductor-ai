"""Dynamic collaboration team planning for requirement-stage review."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(slots=True)
class ReviewSeat:
    """One concrete reviewer seat in a collaboration session."""

    role: str
    seat_id: str
    phase: str
    focus: str


@dataclass(slots=True)
class RequirementTeamPlan:
    """Dynamic team plan derived from requirement complexity."""

    complexity_level: str
    complexity_score: int
    reasons: list[str] = field(default_factory=list)
    peer_seats: list[ReviewSeat] = field(default_factory=list)
    functional_seats: list[ReviewSeat] = field(default_factory=list)


FEATURE_RULES: tuple[tuple[str, tuple[str, ...], str], ...] = (
    ("data", ("data", "schema", "field", "storage", "database", "\u6570\u636e", "\u5b57\u6bb5", "\u5b58\u50a8", "\u6301\u4e45\u5316"), "data model and persistence boundaries"),
    ("api", ("api", "http", "endpoint", "request", "response", "\u63a5\u53e3", "\u8bf7\u6c42", "\u54cd\u5e94", "\u540e\u7aef"), "API contracts and integration boundaries"),
    ("workflow", ("workflow", "approve", "reject", "submit", "transition", "\u6d41\u7a0b", "\u5ba1\u6279", "\u63d0\u4ea4", "\u6d41\u8f6c"), "business process and state transitions"),
    ("roles", ("role", "user", "manager", "admin", "employee", "\u89d2\u8272", "\u7528\u6237", "\u7ba1\u7406\u5458", "\u7ecf\u7406", "\u5458\u5de5"), "permission and actor boundaries"),
    ("validation", ("validation", "invalid", "error", "empty", "edge", "\u6821\u9a8c", "\u65e0\u6548", "\u9519\u8bef", "\u7a7a\u72b6\u6001", "\u8fb9\u754c"), "validation and edge-case behavior"),
    ("export", ("export", "download", "csv", "file", "\u5bfc\u51fa", "\u4e0b\u8f7d", "\u6587\u4ef6", "\u5bfc\u5165"), "file import/export behavior"),
    ("security", ("auth", "login", "permission", "token", "audit", "\u767b\u5f55", "\u6743\u9650", "\u9274\u6743", "\u5ba1\u8ba1"), "security and access-control assumptions"),
)


def plan_requirement_review_team(
    requirement: str,
    *,
    peer_roles: list[str],
    functional_roles: list[str],
) -> RequirementTeamPlan:
    """Plan reviewer seats for requirement clarification based on project complexity."""
    features = _detect_features(requirement)
    score = _complexity_score(requirement, features)
    level = _complexity_level(score)
    reasons = [description for _, _, description in FEATURE_RULES if _feature_name_for_description(description) in features]
    if len(requirement) >= 160:
        reasons.append("long free-form requirement")
    if len(features) >= 4:
        reasons.append("cross-domain requirement")

    peer_seats = [_base_seat(role, "design_peer_review") for role in peer_roles]
    functional_seats = [_base_seat(role, "cross_functional_review") for role in functional_roles]

    if {"data", "export"} & features:
        peer_seats.append(
            ReviewSeat(
                role="designer",
                seat_id="designer.information_architecture",
                phase="design_peer_review",
                focus="Review fields, data lifecycle, filtering/export behavior, and information boundaries.",
            )
        )
    if {"workflow", "roles"} & features:
        peer_seats.append(
            ReviewSeat(
                role="solution_designer",
                seat_id="solution_designer.process",
                phase="design_peer_review",
                focus="Review actor responsibilities, state transitions, approval paths, and process consistency.",
            )
        )
    if "security" in features:
        peer_seats.append(
            ReviewSeat(
                role="solution_designer",
                seat_id="solution_designer.security_boundary",
                phase="design_peer_review",
                focus="Review access-control assumptions, trust boundaries, and audit/security non-goals.",
            )
        )
    if {"api", "data", "workflow"} & features:
        functional_seats.append(
            ReviewSeat(
                role="backend_engineer",
                seat_id="backend_engineer.contracts",
                phase="cross_functional_review",
                focus="Review API/data contracts, persistence impact, failure modes, and backend acceptance signals.",
            )
        )
    if {"data", "workflow", "roles"} <= features:
        functional_seats.append(
            ReviewSeat(
                role="backend_engineer",
                seat_id="backend_engineer.states",
                phase="cross_functional_review",
                focus="Review backend state transitions, persisted status fields, and actor-specific data access.",
            )
        )
    if {"validation", "workflow", "export"} & features:
        functional_seats.append(
            ReviewSeat(
                role="tester",
                seat_id="tester.edge_cases",
                phase="cross_functional_review",
                focus="Review executable acceptance cases, edge cases, regression scope, and observable pass/fail signals.",
            )
        )

    return RequirementTeamPlan(
        complexity_level=level,
        complexity_score=score,
        reasons=_dedupe(reasons),
        peer_seats=_dedupe_seats(peer_seats),
        functional_seats=_dedupe_seats(functional_seats),
    )


def _detect_features(requirement: str) -> set[str]:
    text = requirement.lower()
    return {name for name, terms, _ in FEATURE_RULES if any(term.lower() in text for term in terms)}


def _complexity_score(requirement: str, features: set[str]) -> int:
    score = max(1, len(features))
    if len(requirement) >= 120:
        score += 1
    if len(requirement) >= 240:
        score += 1
    if {"workflow", "roles"} <= features:
        score += 1
    if {"api", "data"} <= features:
        score += 1
    return score


def _complexity_level(score: int) -> str:
    if score <= 2:
        return "simple"
    if score <= 4:
        return "standard"
    return "complex"


def _base_seat(role: str, phase: str) -> ReviewSeat:
    return ReviewSeat(
        role=role,
        seat_id=role,
        phase=phase,
        focus="Review from the default responsibility of this role.",
    )


def _dedupe(values: list[str]) -> list[str]:
    result: list[str] = []
    for value in values:
        if value and value not in result:
            result.append(value)
    return result


def _dedupe_seats(seats: list[ReviewSeat]) -> list[ReviewSeat]:
    result: list[ReviewSeat] = []
    seen: set[str] = set()
    for seat in seats:
        if seat.seat_id in seen:
            continue
        seen.add(seat.seat_id)
        result.append(seat)
    return result


def _feature_name_for_description(description: str) -> str:
    for name, _, item_description in FEATURE_RULES:
        if item_description == description:
            return name
    return ""


__all__ = ["RequirementTeamPlan", "ReviewSeat", "plan_requirement_review_team"]
