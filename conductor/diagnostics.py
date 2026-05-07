"""Platform diagnostics for CLI and LLM-backed Conductor runs."""

from __future__ import annotations

import json
import locale
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
class PlatformDiagnostics:
    """Serializable platform health snapshot."""

    ok: bool
    project_root: str
    config_paths: dict[str, str]
    selected_cli_names: list[str]
    available_cli_names: list[str]
    role_bindings: list[RoleBindingDiagnostic] = field(default_factory=list)
    llm_backends: list[LLMBackendDiagnostic] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, object]:
        """Return a JSON-serializable dictionary."""
        return asdict(self)


ModelProbe = Callable[[str, str | None, float], tuple[str, list[str], int | None, str]]
PreflightProbe = Callable[[str], tuple[bool, str]]


def build_platform_diagnostics(
    cli_config: CLISelectionConfig | None = None,
    project_root: str | Path | None = None,
    llm_runtime_config: LLMRuntimeConfig | None = None,
    probe_llm: bool = False,
    model_probe: ModelProbe | None = None,
    preflight_probe: PreflightProbe | None = None,
) -> PlatformDiagnostics:
    """Build a diagnostic snapshot for the current local platform setup."""
    runtime_cli_config = cli_config or load_cli_selection_config()
    runtime_llm_config = llm_runtime_config or load_llm_runtime_config()
    tools = discover_cli_tools()
    available_by_name = {tool.name: tool.available for tool in tools}
    available_cli_names = [tool.name for tool in tools if tool.available]
    selected_cli_names = list(runtime_cli_config.selected_cli_names)
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
    warnings = _build_warnings(selected_cli_names, available_by_name, role_bindings, llm_backends)
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
        role_bindings=role_bindings,
        llm_backends=llm_backends,
        warnings=warnings,
    )


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
) -> list[str]:
    warnings: list[str] = []
    for cli_name in selected_cli_names:
        if not available_by_name.get(cli_name, False):
            warnings.append(f"Selected CLI `{cli_name}` is not available on PATH.")
    for binding in role_bindings:
        if binding.status in {"not_selected", "missing"}:
            warnings.append(binding.message)
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
    preferred = locale.getpreferredencoding(False)
    return f"preferred={preferred}"


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
    "LLMBackendDiagnostic",
    "PlatformDiagnostics",
    "RoleBindingDiagnostic",
    "build_platform_diagnostics",
]
