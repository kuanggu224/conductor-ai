"""Platform diagnostics for CLI and LLM-backed Conductor runs."""

from __future__ import annotations

import json
import locale
import os
import subprocess
import sys
import urllib.error
import urllib.request
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Callable, Iterable

from conductor.config.cli import CLISelectionConfig, discover_cli_tools, load_cli_selection_config
from conductor.config.defaults import CLI_CONFIG_PATH, LLM_CONFIG_PATH
from conductor.config.execution import EXECUTION_CONFIG_PATH
from conductor.config.llm import LLMRuntimeConfig, load_llm_runtime_config
from conductor.config.system import SYSTEM_CONFIG_PATH
from conductor.io.encoding import utf8_subprocess_environment
from conductor.preflight_gate import read_preflight_gate


@dataclass(slots=True)
class CLIToolDiagnostic:
    """Health information for one discovered Agent CLI tool."""

    name: str
    label: str
    path: str
    available: bool
    selected: bool
    status: str
    version_status: str = "not_checked"
    version_output: str = ""
    version_error: str = ""
    auth_status: str = "not_checked"
    auth_error: str = ""
    recommendation: str = ""


@dataclass(slots=True)
class RoleBindingDiagnostic:
    """Health information for one Agent role CLI binding."""

    role: str
    cli_name: str
    available: bool
    selected: bool
    status: str
    message: str


@dataclass(slots=True)
class LLMBackendDiagnostic:
    """Health information for one OpenAI-compatible LLM backend."""

    backend: str
    enabled: bool
    base_url: str
    model: str
    timeout_seconds: float
    api_key_present: bool
    timeout_status: str = "ok"
    timeout_warning: str = ""
    server_status: str = "not_checked"
    context_length: int | None = None
    available_models: list[str] = field(default_factory=list)
    selected_model_available: bool | None = None
    model_list_error: str = ""
    encoding: str = ""
    preflight_success: bool | None = None
    preflight_error: str = ""
    health_status: str = "not_checked"
    recommendation: str = ""


@dataclass(slots=True)
class EncodingDiagnostic:
    """Runtime encoding information useful for Windows/Linux troubleshooting."""

    preferred_encoding: str
    filesystem_encoding: str
    stdout_encoding: str
    stderr_encoding: str
    python_utf8_mode: int
    pythonioencoding_env: str
    pythonutf8_env: str
    utf8_ready: bool
    warnings: list[str] = field(default_factory=list)
    recommendation: str = ""


@dataclass(slots=True)
class PlatformDiagnostics:
    """Serializable platform health snapshot."""

    ok: bool
    project_root: str
    config_paths: dict[str, str]
    selected_cli_names: list[str]
    available_cli_names: list[str]
    encoding: EncodingDiagnostic
    cli_tools: list[CLIToolDiagnostic] = field(default_factory=list)
    role_bindings: list[RoleBindingDiagnostic] = field(default_factory=list)
    llm_backends: list[LLMBackendDiagnostic] = field(default_factory=list)
    preflight_gate: dict[str, object] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, object]:
        """Return a JSON-serializable dictionary."""
        return asdict(self)


ModelProbe = Callable[[str, str | None, float], tuple[str, list[str], int | None, str]]
PreflightProbe = Callable[[str], tuple[bool, str]]
CLIProbe = Callable[[str, float], tuple[str, str, str]]


def build_platform_diagnostics(
    cli_config: CLISelectionConfig | None = None,
    project_root: str | Path | None = None,
    llm_runtime_config: LLMRuntimeConfig | None = None,
    probe_cli: bool = False,
    cli_probe: CLIProbe | None = None,
    probe_llm: bool = False,
    model_probe: ModelProbe | None = None,
    preflight_probe: PreflightProbe | None = None,
) -> PlatformDiagnostics:
    """Build a diagnostic snapshot for the current local platform setup."""
    runtime_cli_config = cli_config or load_cli_selection_config()
    runtime_llm_config = llm_runtime_config or load_llm_runtime_config()
    tools = discover_cli_tools()
    selected_cli_names = list(runtime_cli_config.selected_cli_names)
    cli_tools = _build_cli_tool_diagnostics(
        tools,
        selected_cli_names=selected_cli_names,
        probe_cli=probe_cli,
        cli_probe=cli_probe,
    )
    available_by_name = {tool.name: tool.available for tool in cli_tools}
    available_cli_names = [tool.name for tool in cli_tools if tool.available]
    role_bindings = [
        _diagnose_role_binding(
            role=role,
            cli_name=cli_name or "",
            selected_cli_names=selected_cli_names,
            available_by_name=available_by_name,
        )
        for role, cli_name in sorted(runtime_cli_config.role_cli_bindings.items())
    ]
    llm_backends = _build_llm_backend_diagnostics(
        runtime_llm_config,
        probe_llm=probe_llm,
        model_probe=model_probe,
        preflight_probe=preflight_probe,
    )
    warnings = _build_warnings(selected_cli_names, available_by_name, role_bindings, llm_backends, cli_tools)
    resolved_project_root = Path(project_root or Path.cwd()).expanduser().resolve()
    return PlatformDiagnostics(
        ok=not warnings,
        project_root=str(resolved_project_root),
        config_paths={
            "cli": str(CLI_CONFIG_PATH),
            "system": str(SYSTEM_CONFIG_PATH),
            "execution": str(EXECUTION_CONFIG_PATH),
            "llm": str(LLM_CONFIG_PATH),
        },
        selected_cli_names=selected_cli_names,
        available_cli_names=available_cli_names,
        encoding=_runtime_encoding_diagnostic(),
        cli_tools=cli_tools,
        role_bindings=role_bindings,
        llm_backends=llm_backends,
        preflight_gate=asdict(read_preflight_gate(resolved_project_root)),
        warnings=warnings,
    )


def build_requirement_llm_preflight_probe(
    runtime_config: LLMRuntimeConfig,
    output_dir: str | Path,
) -> PreflightProbe:
    """Build a lightweight chat-completion probe for enabled LLM diagnostics."""
    from conductor.requirement_benchmark import run_requirement_llm_preflight

    diagnostics_dir = Path(output_dir)

    def probe(backend: str) -> tuple[bool, str]:
        config = runtime_config.local if backend == "local" else runtime_config.cloud
        result = run_requirement_llm_preflight(
            backend=backend,
            config=config,
            output_dir=diagnostics_dir,
        )
        return result.success, result.error

    return probe


def _build_cli_tool_diagnostics(
    tools,
    *,
    selected_cli_names: list[str],
    probe_cli: bool,
    cli_probe: CLIProbe | None,
) -> list[CLIToolDiagnostic]:
    """Build diagnostics for discovered Agent CLI tools."""
    selected = set(selected_cli_names)
    probe = cli_probe or _probe_cli_version
    diagnostics: list[CLIToolDiagnostic] = []
    for tool in tools:
        name = str(getattr(tool, "name", ""))
        label = str(getattr(tool, "label", name))
        path = str(getattr(tool, "path", "") or "")
        available = bool(getattr(tool, "available", False))
        item = CLIToolDiagnostic(
            name=name,
            label=label,
            path=path,
            available=available,
            selected=name in selected,
            status="available" if available else "missing",
        )
        if probe_cli and available and path:
            status, output, error = probe(path, 5.0)
            item.version_status = status
            item.version_output = output
            item.version_error = error
            item.auth_status, item.auth_error = _classify_cli_auth_status(
                cli_name=name,
                version_status=status,
                output=output,
                error=error,
            )
        elif probe_cli and not available:
            item.version_status = "not_available"
            item.auth_status = "not_available"
        item.recommendation = _cli_recommendation(item)
        diagnostics.append(item)
    return diagnostics


def _build_llm_backend_diagnostics(
    runtime_llm_config: LLMRuntimeConfig,
    *,
    probe_llm: bool,
    model_probe: ModelProbe | None,
    preflight_probe: PreflightProbe | None,
) -> list[LLMBackendDiagnostic]:
    diagnostics: list[LLMBackendDiagnostic] = []
    for backend, config in (
        ("local", runtime_llm_config.local),
        ("cloud", runtime_llm_config.cloud),
    ):
        item = LLMBackendDiagnostic(
            backend=backend,
            enabled=config.enabled,
            base_url=config.base_url,
            model=config.model_name,
            timeout_seconds=config.timeout_seconds,
            api_key_present=bool(config.api_key),
            timeout_status=_timeout_status(config.timeout_seconds),
            timeout_warning=_timeout_warning(config.timeout_seconds),
            encoding=_runtime_encoding_summary(),
        )
        if not config.enabled:
            item.health_status = "disabled"
            item.recommendation = f"Enable the {backend} LLM backend before using it for Agent execution."
            diagnostics.append(item)
            continue
        item.health_status = _llm_health_status(item)
        item.recommendation = _llm_recommendation(item)
        if probe_llm and config.enabled and item.timeout_status == "invalid":
            item.server_status = "invalid_timeout"
            item.model_list_error = item.timeout_warning
            item.health_status = _llm_health_status(item)
            item.recommendation = _llm_recommendation(item)
        elif probe_llm and config.enabled:
            probe = model_probe or _probe_openai_models
            status, models, context_length, error = probe(config.base_url, config.api_key, config.timeout_seconds)
            item.server_status = status
            item.available_models = models
            item.context_length = context_length
            item.selected_model_available = config.model_name in models if models else None
            if error:
                item.model_list_error = error
            if preflight_probe is not None:
                success, preflight_error = preflight_probe(backend)
                item.preflight_success = success
                if preflight_error:
                    item.preflight_error = preflight_error
            item.health_status = _llm_health_status(item)
            item.recommendation = _llm_recommendation(item)
        diagnostics.append(item)
    return diagnostics


def _diagnose_role_binding(
    role: str,
    cli_name: str,
    selected_cli_names: list[str],
    available_by_name: dict[str, bool],
) -> RoleBindingDiagnostic:
    if not cli_name:
        return RoleBindingDiagnostic(
            role=role,
            cli_name="",
            available=False,
            selected=False,
            status="unbound",
            message="Role is not bound to a CLI; it may use LLM or mock fallback.",
        )
    available = available_by_name.get(cli_name, False)
    selected = cli_name in selected_cli_names
    if not selected:
        return RoleBindingDiagnostic(
            role=role,
            cli_name=cli_name,
            available=available,
            selected=False,
            status="not_selected",
            message=f"{role} is bound to {cli_name}, but that CLI is not selected.",
        )
    if not available:
        return RoleBindingDiagnostic(
            role=role,
            cli_name=cli_name,
            available=False,
            selected=True,
            status="missing",
            message=f"{role} is bound to {cli_name}, but it was not found on PATH.",
        )
    return RoleBindingDiagnostic(
        role=role,
        cli_name=cli_name,
        available=True,
        selected=True,
        status="ready",
        message=f"{role} will use {cli_name} CLI.",
    )


def _build_warnings(
    selected_cli_names: list[str],
    available_by_name: dict[str, bool],
    role_bindings: list[RoleBindingDiagnostic],
    llm_backends: list[LLMBackendDiagnostic],
    cli_tools: list[CLIToolDiagnostic],
) -> list[str]:
    warnings: list[str] = []
    for cli_name in selected_cli_names:
        if not available_by_name.get(cli_name, False):
            warnings.append(f"Selected CLI `{cli_name}` is not available on PATH.")
    for binding in role_bindings:
        if binding.status in {"not_selected", "missing"}:
            warnings.append(binding.message)
    for tool in cli_tools:
        if tool.selected and tool.version_status in {"failed", "timeout"}:
            warnings.append(f"Selected CLI `{tool.name}` probe failed: {tool.version_error or tool.version_output}")
        if tool.selected and tool.auth_status == "unauthorized":
            warnings.append(f"Selected CLI `{tool.name}` appears unauthorized: {tool.auth_error}")
    for backend in llm_backends:
        if not backend.enabled:
            continue
        if backend.timeout_status == "invalid":
            warnings.append(f"{backend.backend} LLM timeout is invalid: {backend.timeout_seconds:g}s.")
        if backend.backend == "cloud" and not backend.api_key_present:
            warnings.append(f"Cloud LLM `{backend.model}` is enabled but API key is missing.")
        if backend.server_status == "unreachable" and backend.preflight_success is not True:
            warnings.append(f"{backend.backend} LLM server is unreachable: {backend.model_list_error}")
        if backend.selected_model_available is False:
            warnings.append(
                f"{backend.backend} LLM model `{backend.model}` is not listed by the configured endpoint."
            )
        if backend.preflight_success is False:
            warnings.append(f"{backend.backend} LLM preflight failed: {backend.preflight_error}")
    return warnings


def _llm_health_status(backend: LLMBackendDiagnostic) -> str:
    """Classify one LLM backend into a UI/API friendly health status."""
    if not backend.enabled:
        return "disabled"
    if backend.timeout_status == "invalid":
        return "failed"
    if backend.preflight_success is False:
        return "failed"
    if backend.server_status == "unreachable":
        return "failed"
    if backend.selected_model_available is False:
        return "warning"
    if backend.preflight_success is True:
        return "ready"
    if backend.server_status == "reachable":
        return "reachable"
    if backend.server_status == "models_unavailable":
        return "models_unavailable"
    return "configured"


def _llm_recommendation(backend: LLMBackendDiagnostic) -> str:
    """Return a short remediation hint for one LLM backend."""
    if not backend.enabled:
        return f"Enable the {backend.backend} LLM backend before using it for Agent execution."
    if backend.timeout_status == "invalid":
        return "Set a positive LLM timeout before probing or running Agent execution."
    if backend.backend == "cloud" and not backend.api_key_present:
        return "Fill the cloud API key in local settings before running cloud LLM Agents."
    if backend.preflight_success is False:
        return "Check base URL, API key, model name, timeout, and provider quota, then run preflight again."
    if backend.server_status == "unreachable":
        return "Check whether the model server is running and reachable from this machine."
    if backend.selected_model_available is False:
        return "Choose one of the listed models or update the configured model name."
    if backend.context_length is not None and backend.context_length < 8192:
        return "The endpoint is reachable, but context length may be too small for multi-Agent prompts."
    if backend.preflight_success is True:
        return "Backend preflight passed and is ready for controlled Agent execution."
    if backend.server_status == "models_unavailable":
        return "The /models endpoint is unavailable; rely on preflight to verify this provider."
    if backend.timeout_status == "low":
        return "Configured timeout is low for multi-Agent prompts; increase it before longer real runs."
    return "Run diagnostics with probe_llm and preflight_llm before real Agent execution."


def _runtime_encoding_summary() -> str:
    diagnostic = _runtime_encoding_diagnostic()
    return (
        f"preferred={diagnostic.preferred_encoding}, "
        f"stdout={diagnostic.stdout_encoding}, "
        f"utf8_mode={diagnostic.python_utf8_mode}"
    )


def _runtime_encoding_diagnostic() -> EncodingDiagnostic:
    preferred = locale.getpreferredencoding(False)
    filesystem = sys.getfilesystemencoding()
    stdout = getattr(sys.stdout, "encoding", "") or ""
    stderr = getattr(sys.stderr, "encoding", "") or ""
    python_utf8_mode = int(sys.flags.utf8_mode)
    pythonioencoding = os.environ.get("PYTHONIOENCODING", "")
    pythonutf8 = os.environ.get("PYTHONUTF8", "")
    warnings = _encoding_warnings(
        preferred_encoding=preferred,
        filesystem_encoding=filesystem,
        stdout_encoding=stdout,
        stderr_encoding=stderr,
        python_utf8_mode=python_utf8_mode,
        pythonioencoding_env=pythonioencoding,
        pythonutf8_env=pythonutf8,
    )
    return EncodingDiagnostic(
        preferred_encoding=preferred,
        filesystem_encoding=filesystem,
        stdout_encoding=stdout,
        stderr_encoding=stderr,
        python_utf8_mode=python_utf8_mode,
        pythonioencoding_env=pythonioencoding,
        pythonutf8_env=pythonutf8,
        utf8_ready=not warnings,
        warnings=warnings,
        recommendation=(
            "Runtime encodings are UTF-8 ready."
            if not warnings
            else "Enable UTF-8 stdio before reading Chinese logs or running external Agent CLIs."
        ),
    )


def _timeout_status(timeout_seconds: float) -> str:
    """Return a coarse health status for a configured LLM timeout."""
    if timeout_seconds <= 0:
        return "invalid"
    if timeout_seconds < 10:
        return "low"
    return "ok"


def _timeout_warning(timeout_seconds: float) -> str:
    """Return a diagnostic warning for risky timeout settings."""
    if timeout_seconds <= 0:
        return "timeout_seconds must be greater than 0"
    if timeout_seconds < 10:
        return "timeout_seconds is below 10s and may fail on multi-Agent prompts"
    return ""


def _encoding_warnings(
    *,
    preferred_encoding: str,
    filesystem_encoding: str,
    stdout_encoding: str,
    stderr_encoding: str,
    python_utf8_mode: int,
    pythonioencoding_env: str,
    pythonutf8_env: str,
) -> list[str]:
    """Return runtime encoding warnings without failing the whole diagnostic snapshot."""
    warnings: list[str] = []
    if not _is_utf8(preferred_encoding):
        warnings.append(f"preferred encoding is not UTF-8: {preferred_encoding or 'unknown'}")
    if not _is_utf8(filesystem_encoding):
        warnings.append(f"filesystem encoding is not UTF-8: {filesystem_encoding or 'unknown'}")
    if stdout_encoding and not _is_utf8(stdout_encoding):
        warnings.append(f"stdout encoding is not UTF-8: {stdout_encoding}")
    if stderr_encoding and not _is_utf8(stderr_encoding):
        warnings.append(f"stderr encoding is not UTF-8: {stderr_encoding}")
    if python_utf8_mode != 1 and pythonutf8_env != "1" and "utf" not in pythonioencoding_env.lower():
        warnings.append("Python UTF-8 mode is not explicitly enabled")
    return warnings


def _is_utf8(value: str) -> bool:
    normalized = value.replace("-", "").replace("_", "").lower()
    return "utf8" in normalized


def _probe_openai_models(
    base_url: str,
    api_key: str | None,
    timeout_seconds: float,
) -> tuple[str, list[str], int | None, str]:
    url = base_url.rstrip("/") + "/models"
    headers = {"Accept": "application/json"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    request = urllib.request.Request(url=url, headers=headers, method="GET")
    try:
        with urllib.request.urlopen(request, timeout=timeout_seconds) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as error:
        body = error.read().decode("utf-8", errors="replace")
        if error.code in {404, 405}:
            return "models_unavailable", [], None, f"HTTP {error.code}: {body[:300]}"
        return "unreachable", [], None, f"HTTP {error.code}: {body[:300]}"
    except Exception as error:
        return "unreachable", [], None, str(error)
    models = _extract_model_names(payload)
    context_length = _extract_context_length(payload, models[0] if models else "")
    return "reachable", models, context_length, ""


def _probe_cli_version(path: str, timeout_seconds: float) -> tuple[str, str, str]:
    """Run a lightweight CLI version probe without invoking agent work."""
    try:
        completed = subprocess.run(
            [path, "--version"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout_seconds,
            env=utf8_subprocess_environment(),
            check=False,
        )
    except subprocess.TimeoutExpired:
        return "timeout", "", f"`{path} --version` timed out after {timeout_seconds:g}s"
    except OSError as error:
        return "failed", "", str(error)
    output = (completed.stdout or completed.stderr or "").strip()
    if completed.returncode != 0:
        return "failed", output[:500], f"exit_code={completed.returncode}"
    return "ok", output[:500], ""


def _classify_cli_auth_status(
    *,
    cli_name: str,
    version_status: str,
    output: str,
    error: str,
) -> tuple[str, str]:
    """Classify common Agent CLI authentication failures from lightweight probe output."""
    text = f"{output}\n{error}".lower()
    auth_terms = (
        "not logged in",
        "not login",
        "login required",
        "please login",
        "please log in",
        "authentication required",
        "not authenticated",
        "unauthorized",
        "401",
        "403",
        "invalid api key",
        "api key missing",
        "missing api key",
        "token expired",
        "no auth token",
        "invalid auth token",
        "expired auth token",
        "oauth error",
    )
    if any(term in text for term in auth_terms):
        return "unauthorized", _cli_auth_error_message(output=output, error=error)
    if version_status == "not_checked":
        return "not_checked", ""
    if version_status == "not_available":
        return "not_available", ""
    if version_status == "ok":
        return "unknown", f"`{cli_name} --version` passed, but authentication was not exercised."
    return "unknown", _cli_auth_error_message(output=output, error=error)


def _cli_auth_error_message(*, output: str, error: str) -> str:
    return (error or output or "authentication status could not be verified").strip()[:500]


def _cli_recommendation(tool: CLIToolDiagnostic) -> str:
    """Return an operator hint for one Agent CLI diagnostic."""
    if not tool.available:
        return f"Install `{tool.name}` or remove it from selected CLI bindings."
    if tool.auth_status == "unauthorized":
        return f"Run the `{tool.name}` login/auth command, refresh credentials, then rerun diagnostics with --probe-cli."
    if tool.version_status == "timeout":
        return f"`{tool.name} --version` timed out; verify the CLI launches without interactive prompts."
    if tool.version_status == "failed":
        return f"Fix `{tool.name} --version` before using it for Agent execution."
    if tool.auth_status == "unknown":
        return f"`{tool.name}` is installed; run a provider-specific auth/status command before long real runs."
    return f"`{tool.name}` is installed; run diagnostics with --probe-cli before real Agent execution."


def _extract_model_names(payload: object) -> list[str]:
    if not isinstance(payload, dict):
        return []
    data = payload.get("data")
    if not isinstance(data, Iterable):
        return []
    names: list[str] = []
    for item in data:
        if not isinstance(item, dict):
            continue
        model_id = item.get("id") or item.get("name")
        if isinstance(model_id, str):
            names.append(model_id)
    return names


def _extract_context_length(payload: object, selected_model: str) -> int | None:
    if not isinstance(payload, dict):
        return None
    data = payload.get("data")
    if not isinstance(data, Iterable):
        return None
    for item in data:
        if not isinstance(item, dict):
            continue
        model_id = item.get("id") or item.get("name")
        if selected_model and model_id != selected_model:
            continue
        for key in ("context_length", "max_context_length", "context_window", "max_position_embeddings"):
            value = item.get(key)
            if isinstance(value, int):
                return value
    return None


__all__ = [
    "CLIToolDiagnostic",
    "LLMBackendDiagnostic",
    "EncodingDiagnostic",
    "PlatformDiagnostics",
    "RoleBindingDiagnostic",
    "build_platform_diagnostics",
    "build_requirement_llm_preflight_probe",
]
