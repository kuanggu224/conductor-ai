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


def remediation_suggestions(failure_type: str, retryable: bool = True, summary: str = "") -> list[str]:
    """Return operator-facing remediation suggestions for a known failure."""
    normalized = (failure_type or FailureType.UNKNOWN.value).lower()
    summary_lower = summary.lower()
    suggestions_by_type = {
        FailureType.TIMEOUT.value: [
            "Increase the CLI or LLM timeout for this role.",
            "Reduce prompt/context size before retrying.",
            "Switch to a faster model or CLI backend if the model is overloaded.",
        ],
        FailureType.CLI_UNAVAILABLE.value: [
            "Run `python -m app.diagnostics --probe-cli` to verify the selected CLI.",
            "Rebind the role to an available CLI or install the missing CLI.",
            "Check whether the CLI version supports the configured model/options.",
        ],
        FailureType.CONFIGURATION_REQUIRED.value: [
            "Run diagnostics for CLI and LLM configuration before retrying.",
            "Fill missing API keys, model names, or role-to-CLI bindings.",
            "Switch the role to mock or LLMHarness only if real CLI execution is not required.",
        ],
        FailureType.NO_CODE_CHANGES.value: [
            "Narrow the WorkItem and explicitly require file edits in the prompt.",
            "Verify the project root and writable workspace are correct.",
            "Retry with a code-capable CLI/model and include expected output files.",
        ],
        FailureType.VALIDATION_FAILED.value: [
            "Inspect validation stdout/stderr and create a focused rework task.",
            "Rerun the validation command locally after fixes.",
            "Ensure tests cover the frozen requirement rather than only smoke checks.",
        ],
        FailureType.CLI_EXECUTION_FAILED.value: [
            "Inspect CLI stdout/stderr and the generated artifact report.",
            "Retry once if the failure looks transient; otherwise switch CLI/model.",
            "Run `python -m app.diagnostics --probe-cli` before another real execution.",
        ],
        FailureType.HARNESS_FAILED.value: [
            "Inspect harness stdout/stderr and confirm required tools are installed.",
            "Run the same command manually inside the project root.",
            "If the harness edited files, create a focused rework task instead of blind retry.",
        ],
        FailureType.TRANSIENT.value: [
            "Retry after confirming network/model server availability.",
            "If repeated, switch to another backend or reduce request size.",
        ],
        FailureType.UNKNOWN.value: [
            "Inspect the latest execution logs and artifact report.",
            "Run platform diagnostics before retrying.",
            "Escalate to manual review if the same WorkItem fails again.",
        ],
    }
    suggestions = list(suggestions_by_type.get(normalized, suggestions_by_type[FailureType.UNKNOWN.value]))
    if not retryable:
        suggestions.insert(0, "Do not auto-retry until the configuration or blocker is fixed.")
    if "context" in summary_lower and normalized not in {FailureType.TIMEOUT.value, FailureType.NO_CODE_CHANGES.value}:
        suggestions.append("Increase context length or reduce upstream artifact context before retrying.")
    if "api key" in summary_lower or "auth" in summary_lower:
        suggestions.append("Verify API key/authentication settings before retrying.")
    return list(dict.fromkeys(suggestions))


__all__ = [
    "FailureDecision",
    "FailureType",
    "classify_cli_failure",
    "classify_harness_failure",
    "configuration_required",
    "format_failure_reason",
    "no_code_changes",
    "parse_retryable_failure",
    "remediation_suggestions",
    "validation_failed",
]
