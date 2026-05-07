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
    server_status: str = "not_checked"
    context_length: int | None = None
    available_models: list[str] = field(default_factory=list)
    model_list_error: str = ""
    encoding: str = ""
    preflight_success: bool | None = None
    preflight_error: str = ""


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
    return PlatformDiagnostics(
        ok=not warnings,
        project_root=str(Path(project_root or Path.cwd()).expanduser().resolve()),
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
        elif probe_cli and not available:
            item.version_status = "not_available"
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
            encoding=_runtime_encoding_summary(),
        )
        if probe_llm and config.enabled:
            probe = model_probe or _probe_openai_models
            status, models, context_length, error = probe(config.base_url, config.api_key, config.timeout_seconds)
            item.server_status = status
            item.available_models = models
            item.context_length = context_length
            if error:
                item.model_list_error = error
            if preflight_probe is not None:
                success, preflight_error = preflight_probe(backend)
                item.preflight_success = success
                if preflight_error:
                    item.preflight_error = preflight_error
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
    for backend in llm_backends:
        if not backend.enabled:
            continue
        if backend.backend == "cloud" and not backend.api_key_present:
            warnings.append(f"Cloud LLM `{backend.model}` is enabled but API key is missing.")
        if backend.server_status == "unreachable" and backend.preflight_success is not True:
            warnings.append(f"{backend.backend} LLM server is unreachable: {backend.model_list_error}")
        if backend.preflight_success is False:
            warnings.append(f"{backend.backend} LLM preflight failed: {backend.preflight_error}")
    return warnings


def _runtime_encoding_summary() -> str:
    diagnostic = _runtime_encoding_diagnostic()
    return (
        f"preferred={diagnostic.preferred_encoding}, "
        f"stdout={diagnostic.stdout_encoding}, "
        f"utf8_mode={diagnostic.python_utf8_mode}"
    )


def _runtime_encoding_diagnostic() -> EncodingDiagnostic:
    return EncodingDiagnostic(
        preferred_encoding=locale.getpreferredencoding(False),
        filesystem_encoding=sys.getfilesystemencoding(),
        stdout_encoding=getattr(sys.stdout, "encoding", "") or "",
        stderr_encoding=getattr(sys.stderr, "encoding", "") or "",
        python_utf8_mode=int(sys.flags.utf8_mode),
        pythonioencoding_env=os.environ.get("PYTHONIOENCODING", ""),
        pythonutf8_env=os.environ.get("PYTHONUTF8", ""),
    )


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
