"""Failure classification and retry policy."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from conductor.harness.models import HarnessResult


class FailureType(StrEnum):
    """Stable failure categories used by Runner and LeadController."""

    TRANSIENT = "transient"
    TIMEOUT = "timeout"
    CLI_UNAVAILABLE = "cli_unavailable"
    CLI_EXECUTION_FAILED = "cli_execution_failed"
    NO_CODE_CHANGES = "no_code_changes"
    VALIDATION_FAILED = "validation_failed"
    CONFIGURATION_REQUIRED = "configuration_required"
    HARNESS_FAILED = "harness_failed"
    UNKNOWN = "unknown"


@dataclass(slots=True)
class FailureDecision:
    """Retry policy result for one failure."""

    failure_type: FailureType
    retryable: bool
    summary: str


def classify_harness_failure(result: HarnessResult) -> FailureDecision:
    """Classify a failed HarnessResult."""
    output = f"{result.stdout or ''}\n{result.stderr or ''}".lower()
    if result.timed_out or "timed out" in output or "timeout" in output:
        return FailureDecision(FailureType.TIMEOUT, True, "Harness timed out")
    return FailureDecision(FailureType.HARNESS_FAILED, True, f"Harness exit_code={result.exit_code}")


def classify_cli_failure(exit_code: int, stdout: str = "", stderr: str = "", timed_out: bool = False) -> FailureDecision:
    """Classify a failed Agent CLI execution."""
    output = f"{stdout}\n{stderr}".lower()
    if timed_out or "timed out" in output or "timeout" in output:
        return FailureDecision(FailureType.TIMEOUT, True, "Agent CLI timed out")
    if "not recognized" in output or "not found" in output or "unsupported reasoning_effort" in output:
        return FailureDecision(FailureType.CLI_UNAVAILABLE, False, "Agent CLI unavailable or incompatible")
    return FailureDecision(FailureType.CLI_EXECUTION_FAILED, True, f"Agent CLI exit_code={exit_code}")


def configuration_required(summary: str) -> FailureDecision:
    """Return a non-retryable configuration failure."""
    return FailureDecision(FailureType.CONFIGURATION_REQUIRED, False, summary)


def no_code_changes() -> FailureDecision:
    """Return retryable no-code-change failure."""
    return FailureDecision(FailureType.NO_CODE_CHANGES, True, "Agent CLI did not produce required code changes")


def validation_failed(result: HarnessResult | None = None) -> FailureDecision:
    """Return retryable post-edit validation failure."""
    if result is None:
        return FailureDecision(FailureType.VALIDATION_FAILED, True, "Validation did not run")
    return FailureDecision(FailureType.VALIDATION_FAILED, True, f"Validation exit_code={result.exit_code}")


def parse_retryable_failure(blocked_reason: str | None) -> bool:
    """Parse retryability marker stored on WorkItem.blocked_reason."""
    if not blocked_reason:
        return True
    if "retryable=false" in blocked_reason:
        return False
    return True


def format_failure_reason(decision: FailureDecision) -> str:
    """Format a compact machine-readable failure reason."""
    retryable = "true" if decision.retryable else "false"
    return f"failure_type={decision.failure_type.value}; retryable={retryable}; summary={decision.summary}"


__all__ = [
    "FailureDecision",
    "FailureType",
    "classify_cli_failure",
    "classify_harness_failure",
    "configuration_required",
    "format_failure_reason",
    "no_code_changes",
    "parse_retryable_failure",
    "validation_failed",
]
