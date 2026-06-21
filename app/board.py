"""Board API entrypoint."""

from __future__ import annotations

import json
import sys
from dataclasses import asdict, dataclass, replace
from contextlib import asynccontextmanager
from pathlib import Path
from threading import Lock, Thread
from typing import Annotated, Callable

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse, PlainTextResponse, StreamingResponse
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field, StrictBool, StringConstraints

from conductor.agents.llm import LLMHTTPConfig
from conductor.board.service import BoardService
from conductor.config.cli import (
    CLISelectionConfig,
    build_cli_options,
    build_role_cli_binding_options,
    load_cli_selection_config,
    save_cli_selection_config,
)
from conductor.config.execution import (
    ExecutionScopeConfig,
    RunProfile,
    load_execution_scope_config,
    resolve_run_profile,
    save_execution_scope_config,
)
from conductor.config.llm import (
    LLMUsagePolicy,
    LLMRuntimeConfig,
    build_default_hybrid_llm_backend,
    get_llm_provider_preset,
    list_llm_provider_presets,
    load_llm_runtime_config,
    save_llm_runtime_config,
)
from conductor.control.human import HumanControlService
from conductor.controller.engine import ConductorEngine
from conductor.diagnostics import build_platform_diagnostics, build_requirement_llm_preflight_probe
from conductor.domain.models import ProjectStatus, SharedProjectState, TaskAssignment, TaskAssignmentStatus
from conductor.io.encoding import configure_utf8_stdio
from conductor.io.requirements import RequirementInputError, load_requirement_text
from conductor.requirement_benchmark import run_requirement_llm_preflight
from conductor.task_center.artifacts import create_task_return_artifact
from conductor.task_center.commands import build_task_return_commands
from conductor.task_center.context import TaskContextBuilder
from conductor.task_center.prompts import resolve_task_prompt_path, write_task_prompt_file
from conductor.task_center.service import DEFAULT_STALE_CLAIMED_AFTER_SECONDS, TaskCenterError, TaskCenterService
from conductor.todo.service import (
    TODO_SESSION_COOKIE,
    create_todo_service,
    TodoListStatus,
    TodoService,
    ensure_todo_registry,
    reset_todo_registry,
    resolve_todo_service,
)

configure_utf8_stdio()


TodoTitle = Annotated[str, StringConstraints(strict=True, strip_whitespace=True, min_length=1, max_length=200)]
TodoContent = Annotated[str, StringConstraints(strict=True, strip_whitespace=True, max_length=1000)]
OptionalTodoTitle = TodoTitle | None
OptionalTodoContent = TodoContent | None

FOLDER_PICKER_EXCLUDED_DIRS = {
    ".conductor",
    ".conductor_logs",
    ".git",
    ".hg",
    ".mypy_cache",
    ".pytest_cache",
    ".pytest_tmp",
    ".ruff_cache",
    ".svn",
    ".tox",
    ".venv",
    "__pycache__",
    "build",
    "dist",
    "env",
    "node_modules",
    "venv",
}


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Internal helper."""
    reset_todo_registry(app.state)
    ensure_todo_registry(app.state)["default"] = create_todo_service()
    yield


app = FastAPI(title="Conductor Board", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:5173",
        "http://127.0.0.1:5173",
        "http://localhost:4173",
        "http://127.0.0.1:4173",
        "http://localhost:4174",
        "http://127.0.0.1:4174",
        "http://localhost:4175",
        "http://127.0.0.1:4175",
        "http://localhost:4176",
        "http://127.0.0.1:4176",
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

LLM_DISABLED_RUN_PROFILES = {
    RunProfile.MOCK.value,
    RunProfile.API_MOCK.value,
    RunProfile.API_SQLITE.value,
}
board_service = BoardService()
engine = ConductorEngine(run_profile=load_execution_scope_config().run_profile)


class TodoCreateRequest(BaseModel):
    """Internal helper."""

    title: TodoTitle
    content: TodoContent = ""
    completed: StrictBool = False


class TodoUpdateRequest(BaseModel):
    """Internal helper."""

    title: OptionalTodoTitle = None
    completed: StrictBool | None = None
    content: OptionalTodoContent = None


class TaskClaimRequest(BaseModel):
    """Internal helper."""

    agent_id: TodoTitle
    claim_reason: TodoContent = ""
    include_context: StrictBool = False
    include_context_content: StrictBool = True
    max_context_content_chars: int = 12000
    context_format: str = "json"
    prompt_file: str = ""
    lease_seconds: int = 0


class TaskClaimNextRequest(TaskClaimRequest):
    """Internal helper."""

    role: OptionalTodoTitle = None


class TaskClaimBatchRequest(TaskClaimNextRequest):
    """Internal helper."""

    limit: int = 1


class TaskReturnRequest(BaseModel):
    """Internal helper."""

    agent_id: OptionalTodoTitle = None
    claim_token: str = ""
    result_summary: TodoContent = ""
    output_artifact_ids: list[str] = Field(default_factory=list)
    output_artifact_content: str = ""
    output_artifact_kind: str = "external_result"
    output_artifact_title: str = ""
    blocked_reason: TodoContent = ""


class TaskHeartbeatRequest(BaseModel):
    """Internal helper."""

    agent_id: OptionalTodoTitle = None
    claim_token: str = ""
    lease_seconds: int | None = None


class TaskReleaseRequest(BaseModel):
    """Internal helper."""

    agent_id: OptionalTodoTitle = None
    claim_token: str = ""
    release_reason: TodoContent = ""


class TaskReleaseStaleRequest(TaskReleaseRequest):
    """Internal helper."""

    stale_after_seconds: int = DEFAULT_STALE_CLAIMED_AFTER_SECONDS


class TaskSweepRequest(BaseModel):
    """Internal helper."""

    stale_after_seconds: int = DEFAULT_STALE_CLAIMED_AFTER_SECONDS
    expired_lease_release_reason: TodoContent = "expired task lease"
    stale_release_reason: TodoContent = "stale claimed assignment"


class HumanControlRequest(BaseModel):
    """Internal helper."""

    actor: TodoTitle = "human"
    reason: TodoContent = ""
    controller_action: str = ""
    stage: str = ""
    workitem_id: str = ""
    payload: dict[str, object] = Field(default_factory=dict)


@dataclass(slots=True)
class ProjectTaskStatus:
    """Internal helper."""

    running: bool = False
    action: str = ""
    action_label: str = ""
    message: str = "绌洪棽"
    error: str | None = None


task_statuses: dict[str, ProjectTaskStatus] = {}
task_lock = Lock()


def get_todo_service(request: Request | None = None) -> TodoService:
    """Internal helper."""
    if request is None:
        registry = ensure_todo_registry(app.state)
        service = registry.get("default")
        if service is None:
            return _ensure_todo_service()
        return service
    cached_service = getattr(request.state, "todo_service", None)
    if cached_service is not None:
        return cached_service
    _, service = resolve_todo_service(request)
    return service


def _ensure_todo_service() -> TodoService:
    """Internal helper."""
    service = create_todo_service()
    ensure_todo_registry(app.state)["default"] = service
    return service


@app.middleware("http")
async def todo_session_middleware(request: Request, call_next):
    """Internal helper."""
    session_id, service = resolve_todo_service(request)
    request.state.todo_session_id = session_id
    request.state.todo_service = service
    response = await call_next(request)
    response.set_cookie(
        TODO_SESSION_COOKIE,
        session_id,
        httponly=True,
        samesite="lax",
        path="/",
    )
    return response


def refresh_engine_llm_backend() -> None:
    """Internal helper."""
    config = load_llm_runtime_config()
    profile_disables_llm = engine.run_profile in LLM_DISABLED_RUN_PROFILES and engine.llm_harness_backend is None
    if profile_disables_llm:
        config = replace(config, usage=replace(config.usage, runner_enabled=False))
    llm_backend = build_default_hybrid_llm_backend(config)
    engine.llm_runtime_config = config
    engine.runner.llm_usage_policy = config.usage
    engine.collaboration_runner.use_llm = not profile_disables_llm
    engine.registry.llm_backend = llm_backend
    for agent in engine.registry.agents:
        agent.llm_backend = llm_backend


def refresh_engine_execution_scope() -> None:
    """Internal helper."""
    config = load_execution_scope_config()
    engine.execution_scope_config = config
    engine.planner.scope_config = config
    engine.collaboration_runner.policy.enabled = config.design_collaboration_enabled
    _apply_engine_run_profile(config.run_profile)


def _apply_engine_run_profile(profile: str) -> None:
    """Internal helper."""
    resolved = resolve_run_profile(profile)
    engine.run_profile = resolved.profile.value
    engine.runner.enable_api_mock_delivery = resolved.enable_api_mock_delivery
    engine.runner.enable_api_sqlite_delivery = resolved.enable_api_sqlite_delivery
    engine.runner.require_real_design_outputs = resolved.require_real_design_outputs
    engine.runner.require_real_code_outputs = (
        bool(engine.cli_selection_config.selected_cli_names)
        if resolved.require_real_code_outputs is None
        else resolved.require_real_code_outputs
    )
    engine.collaboration_runner.require_real_outputs = resolved.require_real_design_outputs


def refresh_engine_cli_config() -> None:
    """Internal helper."""
    config = load_cli_selection_config()
    engine.cli_selection_config = config
    engine.runner.cli_selection_config = config
    engine.runner.agent_cli_executor.cli_selection_config = config
    engine.collaboration_runner.cli_selection_config = config
    engine.collaboration_runner.agent_cli_executor.cli_selection_config = config
    _apply_engine_run_profile(engine.run_profile)


def _execution_scope_has_enabled_stage(config: ExecutionScopeConfig) -> bool:
    """Internal helper."""
    return any(
        [
            config.requirement_design_enabled,
            config.design_detail_enabled,
            config.design_collaboration_enabled,
            config.backend_development_enabled,
            config.testing_enabled,
        ]
    )


def _normalize_run_profile(value: object) -> str:
    """Internal helper."""
    try:
        return RunProfile(str(value or RunProfile.MOCK.value)).value
    except ValueError as error:
        raise HTTPException(status_code=422, detail="Unsupported run profile.") from error


def _run_profile_options_payload() -> list[dict[str, str]]:
    """Build run profile options with user-facing execution semantics."""
    descriptions = {
        RunProfile.MOCK.value: "Mock execution for workflow demos.",
        RunProfile.API_MOCK.value: "Built-in API mock delivery.",
        RunProfile.API_SQLITE.value: "Built-in SQLite API delivery.",
        RunProfile.DESIGN_CLI_ONLY.value: "Real CLI output required for requirement/design stages.",
        RunProfile.CODE_CLI.value: "Real backend CLI code changes required for development.",
        RunProfile.FULL_CLI.value: "Real CLI output required for design, backend development, and testing.",
    }
    options: list[dict[str, str]] = []
    for profile in RunProfile:
        resolved = resolve_run_profile(profile)
        options.append(
            {
                "value": profile.value,
                "label": profile.value,
                "description": descriptions[profile.value],
                "cli_roles": ", ".join(resolved.cli_roles) if resolved.cli_roles else "none",
                "tone": "warn" if profile == RunProfile.MOCK else ("bad" if resolved.cli_roles else "ok"),
            }
        )
    return options


def _runner_llm_profile_note(run_profile: str) -> str:
    """Return the LLM settings page note for the active execution profile."""
    if run_profile in LLM_DISABLED_RUN_PROFILES:
        return "This run profile uses mock or built-in API delivery; saved LLM settings do not enable runner document mode."
    return "This run profile can use runner LLM document mode when enabled."


def _execution_health_payload() -> dict[str, object]:
    """Summarize saved execution config for API clients."""
    scope_config = load_execution_scope_config()
    cli_config = load_cli_selection_config()
    llm_config = load_llm_runtime_config()
    run_profile = str(getattr(engine, "run_profile", "") or RunProfile.MOCK.value)
    resolved_profile = resolve_run_profile(run_profile)

    selected_cli_names = [name for name in cli_config.selected_cli_names if name]
    selected_cli_set = set(selected_cli_names)
    bound_roles = [role for role, cli_name in cli_config.role_cli_bindings.items() if cli_name and cli_name in selected_cli_set]
    enabled_llm_backends = [
        label
        for label, backend in (("local", llm_config.local), ("cloud", llm_config.cloud))
        if backend.enabled
    ]
    required_cli_roles = list(resolved_profile.cli_roles)
    missing_required_cli_roles = [role for role in required_cli_roles if role not in bound_roles]
    builtin_delivery_enabled = bool(resolved_profile.enable_api_mock_delivery or resolved_profile.enable_api_sqlite_delivery)
    scope_ready = _execution_scope_has_enabled_stage(scope_config)
    llm_runner_ready = bool(enabled_llm_backends and llm_config.usage.runner_enabled)
    mock_profile = resolved_profile.profile == RunProfile.MOCK
    real_executor_ready = (
        builtin_delivery_enabled
        or mock_profile
        or (required_cli_roles and not missing_required_cli_roles)
        or (not required_cli_roles and bool(bound_roles or llm_runner_ready))
    )

    warnings: list[str] = []
    if not scope_ready:
        warnings.append("execution scope has no enabled stage")
    if mock_profile:
        warnings.append("current profile is mock")
    if missing_required_cli_roles:
        warnings.append("missing required CLI roles: " + ", ".join(missing_required_cli_roles))
    if not builtin_delivery_enabled and not selected_cli_names:
        warnings.append("no Agent CLI selected")

    return {
        "run_profile": run_profile,
        "profile_label": f"Profile {run_profile}",
        "profile_cli_roles": required_cli_roles,
        "profile_requires_cli": bool(required_cli_roles),
        "profile_has_internal_executor": builtin_delivery_enabled or mock_profile,
        "selected_cli_names": selected_cli_names,
        "bound_roles": bound_roles,
        "missing_required_cli_roles": missing_required_cli_roles,
        "enabled_llm_backends": enabled_llm_backends,
        "llm_runner_enabled": llm_config.usage.runner_enabled,
        "llm_runner_ready": llm_runner_ready,
        "scope_ready": scope_ready,
        "real_executor_ready": bool(real_executor_ready),
        "warnings": warnings,
    }


def _platform_status_payload() -> dict[str, object]:
    """Return a lightweight platform status payload for frontend connection checks."""
    execution_health = _execution_health_payload()
    projects = engine.list_projects()
    return {
        "ok": True,
        "service": "Conductor Board",
        "api": "online",
        "project_count": len(projects),
        "run_profile": execution_health["run_profile"],
        "real_executor_ready": execution_health["real_executor_ready"],
        "warnings": execution_health["warnings"],
        "endpoints": {
            "projects": "/api/projects",
            "diagnostics": "/api/diagnostics",
            "settings": "/api/settings/execution",
            "todos": "/api/todos",
        },
        "execution_health": execution_health,
    }


def _sanitize_cli_selection_config(config: CLISelectionConfig) -> CLISelectionConfig:
    """Internal helper."""
    selected = [name for name in config.selected_cli_names if name]
    selected_set = set(selected)
    sanitized_bindings = {
        role: (cli_name if cli_name in selected_set else None)
        for role, cli_name in config.role_cli_bindings.items()
    }
    return CLISelectionConfig(
        selected_cli_names=selected,
        role_cli_bindings=sanitized_bindings,
        codex_model=config.codex_model,
        codex_reasoning_effort=config.codex_reasoning_effort,
        aspirecode_model=config.aspirecode_model,
    )


def match_llm_provider_preset_id(base_url: str, model_name: str, backend: str) -> str:
    """Internal helper."""
    normalized_base_url = base_url.rstrip("/")
    for preset in list_llm_provider_presets(backend):
        if preset.base_url.rstrip("/") == normalized_base_url and preset.model_name == model_name:
            return preset.id
    return ""


def redacted_llm_runtime_config(config: LLMRuntimeConfig) -> LLMRuntimeConfig:
    """Internal helper."""
    return LLMRuntimeConfig(
        local=replace(config.local, api_key=None),
        cloud=replace(config.cloud, api_key=None),
        usage=config.usage,
        pricing=config.pricing,
    )


def llm_settings_payload(config: LLMRuntimeConfig) -> dict[str, object]:
    """Internal helper."""
    payload = asdict(redacted_llm_runtime_config(config))
    payload["local"]["api_key_present"] = bool(config.local.api_key)
    payload["cloud"]["api_key_present"] = bool(config.cloud.api_key)
    payload["provider_presets"] = [asdict(preset) for preset in list_llm_provider_presets()]
    return payload


def _payload_api_key(payload: dict, current_api_key: str | None) -> str | None:
    """Internal helper."""
    if "api_key" not in payload:
        return current_api_key
    value = payload.get("api_key")
    if value is None:
        return current_api_key
    text = str(value).strip()
    return text or current_api_key


def _form_api_key(form: dict[str, list[str]], name: str, current_api_key: str | None) -> str | None:
    """Internal helper."""
    value = _optional_form_value(form, name)
    return value if value is not None else current_api_key


def llm_config_from_settings_payload(payload: dict, existing_config: LLMRuntimeConfig) -> LLMRuntimeConfig:
    """Internal helper."""
    local_payload = dict(payload.get("local", {}))
    cloud_payload = dict(payload.get("cloud", {}))
    local_preset = get_llm_provider_preset(local_payload.get("preset_id"))
    cloud_preset = get_llm_provider_preset(cloud_payload.get("preset_id"))
    return LLMRuntimeConfig(
        local=LLMHTTPConfig(
            base_url=str((local_preset.base_url if local_preset else None) or local_payload.get("base_url") or "http://127.0.0.1:11434/v1"),
            model_name=str((local_preset.model_name if local_preset else None) or local_payload.get("model_name") or "local-demo-model"),
            api_key=_payload_api_key(local_payload, existing_config.local.api_key),
            timeout_seconds=float((local_preset.timeout_seconds if local_preset else None) or local_payload.get("timeout_seconds") or 30.0),
            enabled=bool(local_payload.get("enabled", False)),
        ),
        cloud=LLMHTTPConfig(
            base_url=str((cloud_preset.base_url if cloud_preset else None) or cloud_payload.get("base_url") or "https://api.openai.com/v1"),
            model_name=str((cloud_preset.model_name if cloud_preset else None) or cloud_payload.get("model_name") or "gpt-demo-model"),
            api_key=_payload_api_key(cloud_payload, existing_config.cloud.api_key),
            timeout_seconds=float((cloud_preset.timeout_seconds if cloud_preset else None) or cloud_payload.get("timeout_seconds") or 30.0),
            enabled=bool(cloud_payload.get("enabled", False)),
        ),
        usage=LLMUsagePolicy(
            runner_enabled=bool(payload.get("usage", {}).get("runner_enabled", False)),
            runner_allowed_roles=list(payload.get("usage", {}).get("runner_allowed_roles", ["designer", "backend_engineer", "tester"])),
            runner_allowed_kinds=list(payload.get("usage", {}).get("runner_allowed_kinds", [
                "design_overview",
                "api_design",
                "test_design",
                "generic_implementation",
                "api_implementation",
                "data_implementation",
                "acceptance_check",
                "automated_test",
                "api_validation",
            ])),
            preferred_backend=str(payload.get("usage", {}).get("preferred_backend", "cloud")),
        ),
        pricing=existing_config.pricing,
    )


def get_project_task_status(project_id: str) -> ProjectTaskStatus:
    """Internal helper."""
    with task_lock:
        return task_statuses.get(project_id, ProjectTaskStatus())


def _task_status_payload(project_id: str) -> dict[str, object]:
    """Internal helper."""
    status = get_project_task_status(project_id)
    return {
        "running": status.running,
        "action": status.action,
        "action_label": status.action_label,
        "message": status.message,
        "error": status.error,
    }


def _workitem_status_summary(snapshot) -> dict[str, int]:
    """Internal helper."""
    done_count = sum(1 for item in snapshot.workitems if item.status == "done")
    failed_count = sum(1 for item in snapshot.workitems if item.status == "failed")
    running_count = sum(1 for item in snapshot.workitems if item.status == "running")
    pending_count = sum(1 for item in snapshot.workitems if item.status == "pending")
    return {
        "total": len(snapshot.workitems),
        "done": done_count,
        "failed": failed_count,
        "running": running_count,
        "pending": pending_count,
        "open": pending_count + running_count,
    }


def _workitem_tail_payload(snapshot) -> list[dict[str, object]]:
    """Internal helper."""
    return [
        asdict(workitem)
        for workitem in snapshot.workitems[:7]
    ]


def _artifact_tail_payload(snapshot) -> list[dict[str, object]]:
    """Internal helper."""
    return [
        asdict(artifact)
        for artifact in reversed(snapshot.artifacts[-6:])
    ]


def _snapshot_payload(project_id: str):
    """Internal helper."""
    state = _require_project_state(project_id)
    snapshot = board_service.build_snapshot(
        state,
        cli_config=engine.cli_selection_config,
        llm_runtime_config=engine.llm_runtime_config,
    )
    return snapshot, asdict(snapshot)


def project_live_state(project_id: str) -> JSONResponse:
    """Return a compact polling payload for frontend live-status panels."""
    snapshot, snapshot_payload = _snapshot_payload(project_id)
    stream_snapshot = engine.runtime_stream_store.snapshot(project_id)
    return JSONResponse(
        {
            "project_id": project_id,
            "project_status": snapshot.project_status,
            "project_status_label": snapshot.project_status_label,
            "current_stage": snapshot.current_stage,
            "current_stage_label": snapshot.current_stage_label,
            "task_status": _task_status_payload(snapshot.project_id),
            "workitems": _workitem_status_summary(snapshot),
            "recent_workitems": _workitem_tail_payload(snapshot),
            "recent_artifacts": _artifact_tail_payload(snapshot),
            "human_control": snapshot_payload["human_control"],
            "runtime_stream": asdict(stream_snapshot),
            "snapshot": snapshot_payload,
        }
    )


def _human_control_response_payload(project_id: str) -> dict[str, object]:
    """Internal helper."""
    snapshot, snapshot_payload = _snapshot_payload(project_id)
    return {
        "project_id": project_id,
        "human_control": snapshot_payload["human_control"],
        "snapshot": snapshot_payload,
        "task_status": _task_status_payload(snapshot.project_id),
    }


def _human_gate_payload(payload: HumanControlRequest, state: SharedProjectState) -> dict[str, object]:
    """Internal helper."""
    gate_payload = dict(payload.payload)
    if payload.controller_action:
        gate_payload["controller_action"] = payload.controller_action
    if payload.stage:
        gate_payload["stage"] = payload.stage
    elif payload.controller_action and "stage" not in gate_payload:
        gate_payload["stage"] = state.current_stage or ""
    return gate_payload


def _human_gate_payload_or_active(
    payload: HumanControlRequest,
    state: SharedProjectState,
    service: HumanControlService,
) -> dict[str, object]:
    """Internal helper."""
    explicit = _human_gate_payload(payload, state)
    if explicit:
        return explicit
    active = service.active_action(state)
    if active and active.payload:
        return dict(active.payload)
    return {}


def _require_project_state(project_id: str):
    """Internal helper."""
    try:
        return engine.get_project(project_id)
    except KeyError as error:
        raise HTTPException(status_code=404, detail=f"Project not found: {project_id}") from error


def _is_folder_picker_visible(path: Path) -> bool:
    """Internal helper."""
    name = path.name
    if not name:
        return True
    if name in FOLDER_PICKER_EXCLUDED_DIRS:
        return False
    if name.startswith("."):
        return False
    return True


def start_project_task(project_id: str, action: str, action_label: str, target: Callable[[str], object]) -> bool:
    """Internal helper."""
    with task_lock:
        current = task_statuses.get(project_id)
        if current and current.running:
            return False
        task_statuses[project_id] = ProjectTaskStatus(
            running=True,
            action=action,
            action_label=action_label,
            message=f"{action_label} started.",
        )
    Thread(
        target=run_project_task,
        args=(project_id, action, action_label, target),
        daemon=True,
    ).start()
    return True


def _project_human_hold_reason(project_id: str) -> str | None:
    """Internal helper."""
    try:
        state = _require_project_state(project_id)
    except HTTPException as error:
        if error.status_code == 404:
            return None
        raise
    return HumanControlService(engine.state_store).controller_hold_reason(state)


def _project_run_block_reason(project_id: str, *, require_project: bool = True) -> str | None:
    """Internal helper."""
    try:
        state = _require_project_state(project_id)
    except HTTPException as error:
        if error.status_code == 404 and not require_project:
            return None
        raise
    hold_reason = HumanControlService(engine.state_store).controller_hold_reason(state)
    if hold_reason:
        return hold_reason
    if state.project_status == ProjectStatus.COMPLETED:
        return "project_completed"
    if state.project_status == ProjectStatus.BLOCKED:
        return "project_blocked"
    execution_health = _execution_health_payload()
    missing_roles = execution_health.get("missing_required_cli_roles", [])
    if missing_roles:
        return "missing required CLI roles: " + ", ".join(str(role) for role in missing_roles)
    return None


def _blocked_project_task_response(project_id: str, action: str, action_label: str, hold_reason: str) -> JSONResponse:
    status = ProjectTaskStatus(
        running=False,
        action=action,
        action_label=action_label,
        message=f"{action_label} 鏈惎鍔細{hold_reason}",
        error=hold_reason,
    )
    return JSONResponse(
        {
            "project_id": project_id,
            "accepted": False,
            "project_status": engine.get_project(project_id).project_status.value,
            "task_status": asdict(status),
            "human_control": asdict(board_service.build_snapshot(engine.get_project(project_id)).human_control),
        },
        status_code=409,
    )


def run_project_task(project_id: str, action: str, action_label: str, target: Callable[[str], object]) -> None:
    """Internal helper."""
    try:
        target(project_id)
    except Exception as error:
        with task_lock:
            task_statuses[project_id] = ProjectTaskStatus(
                running=False,
                action=action,
                action_label=action_label,
                message=f"{action_label} failed.",
                error=str(error),
            )
        return
    with task_lock:
        task_statuses[project_id] = ProjectTaskStatus(
            running=False,
            action=action,
            action_label=action_label,
            message=f"{action_label} completed.",
        )





@app.get("/api/status")
def platform_status_api() -> JSONResponse:
    """Return a lightweight status payload for frontend connection checks."""
    return JSONResponse(_platform_status_payload())


@app.get("/api/projects")
def list_projects_api() -> JSONResponse:
    """Internal helper."""
    states = engine.list_projects()
    payload = {
        "projects": [asdict(item) for item in board_service.build_project_summaries(states)],
    }
    return JSONResponse(payload)


@app.post("/api/projects")
async def create_project_api(request: Request) -> JSONResponse:
    """Internal helper."""
    payload = await request.json()
    try:
        requirement = load_requirement_text(
            requirement=str(payload.get("requirement", "")),
            requirement_file=payload.get("requirement_file"),
            requirement_json_file=payload.get("requirement_json_file"),
            json_key=str(payload.get("requirement_json_key", "requirement")),
        )
    except RequirementInputError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error
    project_root = payload.get("project_root")
    state = engine.create_project(requirement=requirement, project_root=project_root)
    snapshot, snapshot_payload = _snapshot_payload(state.project.id)
    return JSONResponse(
        {
            "project_id": snapshot.project_id,
            "snapshot": snapshot_payload,
            "task_status": _task_status_payload(snapshot.project_id),
        },
        status_code=201,
    )


@app.get("/api/projects/{project_id}")
def project_detail_api(project_id: str) -> JSONResponse:
    """Internal helper."""
    snapshot, snapshot_payload = _snapshot_payload(project_id)
    return JSONResponse(
        {
            "snapshot": snapshot_payload,
            "task_status": _task_status_payload(snapshot.project_id),
        }
    )


@app.get("/api/projects/{project_id}/artifacts/{artifact_id}")
def project_artifact_detail_api(project_id: str, artifact_id: str) -> JSONResponse:
    """Internal helper."""
    state = _require_project_state(project_id)
    artifact = next((item for item in state.artifacts if item.id == artifact_id), None)
    if artifact is None:
        raise HTTPException(status_code=404, detail=f"Artifact not found: {artifact_id}")
    snapshot, _ = _snapshot_payload(project_id)
    artifact_view = next((item for item in snapshot.artifacts if item.id == artifact_id), None)
    if artifact_view is None:
        raise HTTPException(status_code=404, detail=f"Artifact not found: {artifact_id}")
    payload = asdict(artifact_view)
    payload["content"] = engine.artifact_store.read_content(artifact)
    payload["content_source"] = "file" if artifact.path else "state"
    return JSONResponse({"project_id": project_id, "artifact": payload})


@app.get("/api/projects/{project_id}/human-control")
def project_human_control_api(project_id: str) -> JSONResponse:
    """Internal helper."""
    _require_project_state(project_id)
    snapshot, snapshot_payload = _snapshot_payload(project_id)
    return JSONResponse(
        {
            "project_id": project_id,
            "human_control": snapshot_payload["human_control"],
            "snapshot": snapshot_payload,
        }
    )


@app.post("/api/projects/{project_id}/human-control/pause")
async def pause_project_human_control_api(project_id: str, payload: HumanControlRequest) -> JSONResponse:
    """Internal helper."""
    _require_project_state(project_id)
    service = HumanControlService(engine.state_store)
    state = service.pause(project_id, actor=payload.actor, reason=payload.reason)
    return JSONResponse(_human_control_response_payload(state.project.id))


@app.post("/api/projects/{project_id}/human-control/resume")
async def resume_project_human_control_api(project_id: str, payload: HumanControlRequest) -> JSONResponse:
    """Internal helper."""
    _require_project_state(project_id)
    service = HumanControlService(engine.state_store)
    state = service.resume(project_id, actor=payload.actor, reason=payload.reason)
    return JSONResponse(_human_control_response_payload(state.project.id))


@app.post("/api/projects/{project_id}/human-control/request-approval")
async def request_project_human_approval_api(project_id: str, payload: HumanControlRequest) -> JSONResponse:
    """Internal helper."""
    state = _require_project_state(project_id)
    service = HumanControlService(engine.state_store)
    updated = service.request_approval(
        project_id,
        actor=payload.actor,
        reason=payload.reason,
        workitem_id=payload.workitem_id or None,
        payload=_human_gate_payload(payload, state),
    )
    return JSONResponse(_human_control_response_payload(updated.project.id))


@app.post("/api/projects/{project_id}/human-control/approve")
async def approve_project_human_control_api(project_id: str, payload: HumanControlRequest) -> JSONResponse:
    """Internal helper."""
    state = _require_project_state(project_id)
    service = HumanControlService(engine.state_store)
    updated = service.approve(
        project_id,
        actor=payload.actor,
        reason=payload.reason,
        payload=_human_gate_payload_or_active(payload, state, service),
    )
    return JSONResponse(_human_control_response_payload(updated.project.id))


@app.post("/api/projects/{project_id}/human-control/reject")
async def reject_project_human_control_api(project_id: str, payload: HumanControlRequest) -> JSONResponse:
    """Internal helper."""
    _require_project_state(project_id)
    service = HumanControlService(engine.state_store)
    state = service.reject(project_id, actor=payload.actor, reason=payload.reason)
    return JSONResponse(_human_control_response_payload(state.project.id))


@app.post("/api/projects/{project_id}/human-control/override")
async def override_project_human_control_api(project_id: str, payload: HumanControlRequest) -> JSONResponse:
    """Internal helper."""
    state = _require_project_state(project_id)
    service = HumanControlService(engine.state_store)
    updated = service.override(
        project_id,
        actor=payload.actor,
        reason=payload.reason,
        payload=_human_gate_payload_or_active(payload, state, service),
    )
    return JSONResponse(_human_control_response_payload(updated.project.id))


@app.get("/api/projects/{project_id}/tasks")
def project_tasks_api(
    project_id: str,
    status: str | None = None,
    stale_only: bool = False,
    stale_after_seconds: int = DEFAULT_STALE_CLAIMED_AFTER_SECONDS,
) -> JSONResponse:
    """Internal helper."""
    state = _require_project_state(project_id)
    return JSONResponse(
        _task_center_payload(
            state,
            status=status,
            stale_only=stale_only,
            stale_after_seconds=stale_after_seconds,
        )
    )


@app.get("/api/projects/{project_id}/tasks/summary")
def project_tasks_summary_api(
    project_id: str,
    stale_after_seconds: int = DEFAULT_STALE_CLAIMED_AFTER_SECONDS,
) -> JSONResponse:
    """Internal helper."""
    state = _require_project_state(project_id)
    return JSONResponse(
        {
            "project_id": project_id,
            "stale_after_seconds": stale_after_seconds,
            "summary": _task_center_service().summary(state, stale_after_seconds=stale_after_seconds),
            "snapshot": _snapshot_payload(project_id)[1],
        }
    )


@app.get("/api/projects/{project_id}/tasks/{assignment_id}/agents")
def project_task_agents_api(project_id: str, assignment_id: str) -> JSONResponse:
    """Internal helper."""
    state = _require_project_state(project_id)
    context_builder = TaskContextBuilder(engine.artifact_store)
    try:
        context = context_builder.build(
            state,
            assignment_id,
            service=_task_center_service(),
            include_content=False,
            max_content_chars=0,
        )
    except TaskCenterError as error:
        raise HTTPException(status_code=error.status_code, detail=str(error)) from error
    assignment = context["assignment"]
    workitem = context["workitem"]
    agents = context["eligible_agent_activations"]
    return JSONResponse(
        {
            "project_id": project_id,
            "assignment_id": assignment_id,
            "workitem_id": workitem["id"],
            "stage": workitem["stage"],
            "kind": workitem["kind"],
            "role": assignment["role"],
            "eligible_count": len(agents),
            "agents": agents,
        }
    )


@app.get("/api/projects/{project_id}/agents/{agent_id}/tasks")
def project_agent_tasks_api(project_id: str, agent_id: str, claimable_only: bool = False) -> JSONResponse:
    """Internal helper."""
    state = _require_project_state(project_id)
    payload = _agent_tasks_payload(state, agent_id=agent_id, claimable_only=claimable_only)
    payload["snapshot"] = _snapshot_payload(project_id)[1]
    return JSONResponse(payload)


@app.post("/api/projects/{project_id}/agents/{agent_id}/claim-task")
async def claim_project_agent_task_api(project_id: str, agent_id: str, payload: TaskClaimRequest) -> JSONResponse:
    """Internal helper."""
    _validate_task_prompt_file_request(project_id, payload.prompt_file)
    state = _require_project_state(project_id)
    tasks_payload = _agent_tasks_payload(state, agent_id=agent_id, claimable_only=True)
    tasks = tasks_payload["tasks"]
    if not tasks:
        raise HTTPException(status_code=404, detail=f"No claimable task assignment available for agent {agent_id}")
    assignment_id = tasks[0]["assignment_id"]
    transition = _run_task_center_transition(
        _task_center_service().claim,
        project_id,
        assignment_id=assignment_id,
        agent_id=agent_id,
        claim_reason=payload.claim_reason,
        lease_seconds=payload.lease_seconds,
    )
    response = _task_claim_response_payload(project_id, transition.state, transition.assignment, payload)
    response["matched_agent"] = {
        "agent_id": agent_id,
        "instance_id": tasks[0].get("instance_id", ""),
        "scope": tasks[0].get("scope", ""),
        "parallel_safe": tasks[0].get("parallel_safe", False),
        "write_scope": tasks[0].get("write_scope", []),
    }
    return JSONResponse(response)


@app.post("/api/projects/{project_id}/tasks/claim-batch")
async def claim_batch_project_tasks_api(project_id: str, payload: TaskClaimBatchRequest) -> JSONResponse:
    """Internal helper."""
    transition = _run_task_center_transition(
        _task_center_service().claim_batch,
        project_id,
        agent_id=payload.agent_id,
        role=payload.role,
        claim_reason=payload.claim_reason,
        limit=payload.limit,
        lease_seconds=payload.lease_seconds,
    )
    return JSONResponse(
        {
            "project_id": project_id,
            "claimed_count": len(transition.assignments),
            "limit": payload.limit,
            "summary": _task_center_service().summary(transition.state),
            "tasks": [
                _task_assignment_payload(assignment, transition.state)
                for assignment in transition.assignments
            ],
            "snapshot": _snapshot_payload(project_id)[1],
        }
    )


@app.post("/api/projects/{project_id}/tasks/claim-next")
async def claim_next_project_task_api(project_id: str, payload: TaskClaimNextRequest) -> JSONResponse:
    """Internal helper."""
    _validate_task_prompt_file_request(project_id, payload.prompt_file)
    transition = _run_task_center_transition(
        _task_center_service().claim_next,
        project_id,
        agent_id=payload.agent_id,
        role=payload.role,
        claim_reason=payload.claim_reason,
        lease_seconds=payload.lease_seconds,
    )
    return JSONResponse(
        _task_claim_response_payload(
            project_id,
            transition.state,
            transition.assignment,
            payload,
        )
    )


@app.post("/api/projects/{project_id}/tasks/{assignment_id}/claim")
async def claim_project_task_api(project_id: str, assignment_id: str, payload: TaskClaimRequest) -> JSONResponse:
    """Internal helper."""
    _validate_task_prompt_file_request(project_id, payload.prompt_file)
    transition = _run_task_center_transition(
        _task_center_service().claim,
        project_id,
        assignment_id=assignment_id,
        agent_id=payload.agent_id,
        claim_reason=payload.claim_reason,
        lease_seconds=payload.lease_seconds,
    )
    return JSONResponse(
        _task_claim_response_payload(
            project_id,
            transition.state,
            transition.assignment,
            payload,
        )
    )


@app.get("/api/projects/{project_id}/tasks/{assignment_id}/context")
def project_task_context_api(
    project_id: str,
    assignment_id: str,
    include_content: bool = True,
    max_content_chars: int = 12000,
    format: str = "json",
):
    """Internal helper."""
    if format not in {"json", "markdown"}:
        raise HTTPException(status_code=422, detail="format must be 'json' or 'markdown'")
    state = _require_project_state(project_id)
    context_builder = TaskContextBuilder(engine.artifact_store)
    try:
        payload = context_builder.build(
            state,
            assignment_id,
            service=_task_center_service(),
            include_content=include_content,
            max_content_chars=max_content_chars,
        )
    except TaskCenterError as error:
        raise HTTPException(status_code=error.status_code, detail=str(error)) from error
    if format == "markdown":
        return PlainTextResponse(context_builder.render_markdown(payload), media_type="text/markdown")
    return JSONResponse(payload)


@app.post("/api/projects/{project_id}/tasks/{assignment_id}/complete")
async def complete_project_task_api(project_id: str, assignment_id: str, payload: TaskReturnRequest) -> JSONResponse:
    """Internal helper."""
    output_artifact_ids = _task_return_output_artifact_ids(project_id, assignment_id, payload)
    transition = _run_task_center_transition(
        _task_center_service().complete,
        project_id,
        assignment_id=assignment_id,
        result_summary=payload.result_summary,
        output_artifact_ids=output_artifact_ids,
        agent_id=payload.agent_id or "",
        claim_token=payload.claim_token,
    )
    return JSONResponse(
        {
            "project_id": project_id,
            "summary": _task_center_service().summary(transition.state),
            "task": _task_assignment_payload(transition.assignment, transition.state),
            "snapshot": _snapshot_payload(project_id)[1],
        }
    )


@app.post("/api/projects/{project_id}/tasks/{assignment_id}/fail")
async def fail_project_task_api(project_id: str, assignment_id: str, payload: TaskReturnRequest) -> JSONResponse:
    """Internal helper."""
    output_artifact_ids = _task_return_output_artifact_ids(project_id, assignment_id, payload)
    transition = _run_task_center_transition(
        _task_center_service().fail,
        project_id,
        assignment_id=assignment_id,
        result_summary=payload.result_summary,
        output_artifact_ids=output_artifact_ids,
        blocked_reason=payload.blocked_reason,
        agent_id=payload.agent_id or "",
        claim_token=payload.claim_token,
    )
    return JSONResponse(
        {
            "project_id": project_id,
            "summary": _task_center_service().summary(transition.state),
            "task": _task_assignment_payload(transition.assignment, transition.state),
            "snapshot": _snapshot_payload(project_id)[1],
        }
    )


@app.post("/api/projects/{project_id}/tasks/{assignment_id}/heartbeat")
async def heartbeat_project_task_api(
    project_id: str,
    assignment_id: str,
    payload: TaskHeartbeatRequest,
) -> JSONResponse:
    """Internal helper."""
    transition = _run_task_center_transition(
        _task_center_service().heartbeat,
        project_id,
        assignment_id=assignment_id,
        agent_id=payload.agent_id or "",
        claim_token=payload.claim_token,
        lease_seconds=payload.lease_seconds,
    )
    return JSONResponse(
        {
            "project_id": project_id,
            "summary": _task_center_service().summary(transition.state),
            "task": _task_assignment_payload(transition.assignment, transition.state),
            "snapshot": _snapshot_payload(project_id)[1],
        }
    )


@app.post("/api/projects/{project_id}/tasks/{assignment_id}/release")
async def release_project_task_api(project_id: str, assignment_id: str, payload: TaskReleaseRequest) -> JSONResponse:
    """Internal helper."""
    transition = _run_task_center_transition(
        _task_center_service().release,
        project_id,
        assignment_id=assignment_id,
        agent_id=payload.agent_id or "",
        claim_token=payload.claim_token,
        release_reason=payload.release_reason,
    )
    return JSONResponse(
        {
            "project_id": project_id,
            "summary": _task_center_service().summary(transition.state),
            "task": _task_assignment_payload(transition.assignment, transition.state),
            "snapshot": _snapshot_payload(project_id)[1],
        }
    )


@app.post("/api/projects/{project_id}/tasks/release-stale")
async def release_stale_project_tasks_api(project_id: str, payload: TaskReleaseStaleRequest) -> JSONResponse:
    """Internal helper."""
    transition = _run_task_center_transition(
        _task_center_service().release_stale,
        project_id,
        stale_after_seconds=payload.stale_after_seconds,
        release_reason=payload.release_reason or "stale claimed assignment",
    )
    return JSONResponse(
        {
            "project_id": project_id,
            "released_count": len(transition.assignments),
            "stale_after_seconds": payload.stale_after_seconds,
            "summary": _task_center_service().summary(
                transition.state,
                stale_after_seconds=payload.stale_after_seconds,
            ),
            "tasks": [
                _task_assignment_payload(
                    assignment,
                    transition.state,
                    stale_after_seconds=payload.stale_after_seconds,
                )
                for assignment in transition.assignments
            ],
            "snapshot": _snapshot_payload(project_id)[1],
        }
    )


@app.post("/api/projects/{project_id}/tasks/release-expired-leases")
async def release_expired_lease_project_tasks_api(project_id: str, payload: TaskReleaseRequest) -> JSONResponse:
    """Internal helper."""
    transition = _run_task_center_transition(
        _task_center_service().release_expired_leases,
        project_id,
        release_reason=payload.release_reason or "expired task lease",
    )
    return JSONResponse(
        {
            "project_id": project_id,
            "released_count": len(transition.assignments),
            "summary": _task_center_service().summary(transition.state),
            "tasks": [
                _task_assignment_payload(
                    assignment,
                    transition.state,
                )
                for assignment in transition.assignments
            ],
            "snapshot": _snapshot_payload(project_id)[1],
        }
    )


@app.post("/api/projects/{project_id}/tasks/sweep")
async def sweep_project_tasks_api(project_id: str, payload: TaskSweepRequest) -> JSONResponse:
    """Internal helper."""
    transition = _run_task_center_transition(
        _task_center_service().sweep,
        project_id,
        stale_after_seconds=payload.stale_after_seconds,
        expired_lease_release_reason=payload.expired_lease_release_reason,
        stale_release_reason=payload.stale_release_reason,
    )
    return JSONResponse(
        {
            "project_id": project_id,
            "released_count": len(transition.expired_lease_assignments) + len(transition.stale_assignments),
            "expired_lease_released_count": len(transition.expired_lease_assignments),
            "stale_released_count": len(transition.stale_assignments),
            "stale_after_seconds": payload.stale_after_seconds,
            "summary": _task_center_service().summary(
                transition.state,
                stale_after_seconds=payload.stale_after_seconds,
            ),
            "expired_lease_tasks": [
                _task_assignment_payload(assignment, transition.state)
                for assignment in transition.expired_lease_assignments
            ],
            "stale_tasks": [
                _task_assignment_payload(
                    assignment,
                    transition.state,
                    stale_after_seconds=payload.stale_after_seconds,
                )
                for assignment in transition.stale_assignments
            ],
            "snapshot": _snapshot_payload(project_id)[1],
        }
    )


def _task_return_output_artifact_ids(
    project_id: str,
    assignment_id: str,
    payload: TaskReturnRequest,
) -> list[str]:
    output_artifact_ids = list(payload.output_artifact_ids)
    if not payload.output_artifact_content:
        return output_artifact_ids
    state = _require_project_state(project_id)
    service = _task_center_service()
    try:
        assignment = service.validate_return_guard(
            state,
            assignment_id,
            agent_id=payload.agent_id or "",
            claim_token=payload.claim_token,
        )
    except TaskCenterError as error:
        raise HTTPException(status_code=error.status_code, detail=str(error)) from error
    try:
        artifact = create_task_return_artifact(
            state_store=engine.state_store,
            artifact_store=engine.artifact_store,
            state=state,
            assignment=assignment,
            content=payload.output_artifact_content,
            kind=payload.output_artifact_kind,
            title=payload.output_artifact_title,
        )
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    output_artifact_ids.append(artifact.id)
    return output_artifact_ids


def _task_claim_response_payload(
    project_id: str,
    state: SharedProjectState,
    assignment: TaskAssignment,
    payload: TaskClaimRequest,
) -> dict[str, object]:
    response = {
        "project_id": project_id,
        "summary": _task_center_service().summary(state),
        "task": _task_assignment_payload(assignment, state),
    }
    if payload.include_context or payload.prompt_file:
        if payload.context_format not in {"json", "markdown"}:
            raise HTTPException(status_code=422, detail="context_format must be 'json' or 'markdown'")
        context_builder = TaskContextBuilder(engine.artifact_store)
        context = context_builder.build(
            state,
            assignment.id,
            service=_task_center_service(),
            include_content=payload.include_context_content,
            max_content_chars=payload.max_context_content_chars,
        )
        markdown = ""
        if payload.prompt_file or payload.context_format == "markdown":
            markdown = context_builder.render_markdown(context)
        if payload.prompt_file:
            prompt_file = write_task_prompt_file(payload.prompt_file, state.project.project_root, markdown)
            _record_task_prompt_file(state, assignment.id, prompt_file)
            response["prompt_file"] = str(prompt_file)
            response["task"]["prompt_file"] = str(prompt_file)
        if payload.include_context and payload.context_format == "markdown":
            response["context_markdown"] = markdown
        elif payload.include_context:
            response["context"] = context
    response["snapshot"] = _snapshot_payload(project_id)[1]
    return response


def _task_center_payload(
    state: SharedProjectState,
    status: str | None = None,
    stale_only: bool = False,
    stale_after_seconds: int = DEFAULT_STALE_CLAIMED_AFTER_SECONDS,
) -> dict[str, object]:
    """Internal helper."""
    task_center = _task_center_service()
    workitems_by_id = {item.id: item for item in state.workitems}
    artifacts_by_workitem: dict[str, list[dict[str, object]]] = {}
    for artifact in state.artifacts:
        artifacts_by_workitem.setdefault(artifact.workitem_id, []).append(
            {
                "id": artifact.id,
                "kind": artifact.kind,
                "title": artifact.title,
                "path": artifact.path or "",
                "source_backend": artifact.source_backend,
                "version": artifact.version,
            }
        )
    assignments = [
        assignment
        for assignment in state.task_assignments
        if status is None or assignment.status.value == status
    ]
    if stale_only:
        assignments = [
            assignment
            for assignment in assignments
            if task_center.stale_claimed(assignment, stale_after_seconds=stale_after_seconds)
        ]
    return {
        "project_id": state.project.id,
        "status_filter": status or "",
        "stale_only": stale_only,
        "stale_after_seconds": stale_after_seconds,
        "total": len(assignments),
        "summary": task_center.summary(state, stale_after_seconds=stale_after_seconds),
        "tasks": [
            _task_assignment_payload(
                assignment,
                state,
                service=task_center,
                workitem=workitems_by_id.get(assignment.workitem_id),
                artifacts=artifacts_by_workitem.get(assignment.workitem_id, []),
                stale_after_seconds=stale_after_seconds,
            )
            for assignment in assignments
        ],
    }


def _agent_tasks_payload(
    state: SharedProjectState,
    *,
    agent_id: str,
    claimable_only: bool = False,
) -> dict[str, object]:
    """Internal helper."""
    service = _task_center_service()
    activations = [activation for activation in state.agent_activations if activation.agent_id == agent_id]
    workitems_by_id = {item.id: item for item in state.workitems}
    tasks: list[dict[str, object]] = []
    for activation in activations:
        for assignment in state.task_assignments:
            if assignment.role != activation.role:
                continue
            workitem = workitems_by_id.get(assignment.workitem_id)
            if workitem is None:
                continue
            if activation.stage and workitem.stage != activation.stage:
                continue
            if activation.related_workitem_kinds and workitem.kind not in activation.related_workitem_kinds:
                continue
            claimable = service.claimable(state, assignment, agent_id=agent_id)
            if claimable_only and not claimable:
                continue
            write_scope_conflicts = service.write_scope_conflicts(state, assignment, agent_id=agent_id)
            tasks.append(
                {
                    "assignment_id": assignment.id,
                    "workitem_id": workitem.id,
                    "stage": workitem.stage,
                    "kind": workitem.kind,
                    "role": assignment.role,
                    "status": assignment.status.value,
                    "claimable": claimable,
                    "unmet_dependency_ids": service.unmet_dependency_ids(state, assignment),
                    "write_scope_conflict_assignment_ids": write_scope_conflicts,
                    "instance_id": activation.instance_id,
                    "scope": activation.scope,
                    "parallel_safe": activation.parallel_safe,
                    "write_scope": list(activation.write_scope),
                    "claim_command": _dynamic_agent_claim_command(
                        project_root=state.project.project_root,
                        agent_id=agent_id,
                    )
                    if claimable
                    else "",
                    "claim_with_context_command": _dynamic_agent_claim_command(
                        project_root=state.project.project_root,
                        agent_id=agent_id,
                        with_context=True,
                    )
                    if claimable
                    else "",
                    "claim_api_path": f"/api/projects/{state.project.id}/agents/{agent_id}/claim-task"
                    if claimable
                    else "",
                }
            )
    return {
        "project_id": state.project.id,
        "agent_id": agent_id,
        "activation_count": len(activations),
        "claimable_only": claimable_only,
        "task_count": len(tasks),
        "activations": [
            {
                "agent_id": activation.agent_id,
                "role": activation.role,
                "stage": activation.stage,
                "reason": activation.reason,
                "instance_id": activation.instance_id,
                "scope": activation.scope,
                "parallel_safe": activation.parallel_safe,
                "write_scope": list(activation.write_scope),
                "related_workitem_kinds": list(activation.related_workitem_kinds),
            }
            for activation in activations
        ],
        "tasks": tasks,
    }


def _dynamic_agent_claim_command(
    *,
    project_root: str,
    agent_id: str,
    with_context: bool = False,
) -> str:
    parts = [
        "python",
        "-m",
        "app.task_center",
        "claim-for-agent",
        _quote_cli_arg(agent_id),
        "--project-root",
        _quote_cli_arg(project_root or "<project-root>"),
    ]
    if with_context:
        parts.append("--with-context")
    return " ".join(parts)


def _quote_cli_arg(value: object) -> str:
    return '"' + str(value).replace('"', '\\"') + '"'


def _task_assignment_payload(
    assignment: TaskAssignment,
    state: SharedProjectState,
    service: TaskCenterService | None = None,
    workitem=None,
    artifacts: list[dict[str, object]] | None = None,
    stale_after_seconds: int = DEFAULT_STALE_CLAIMED_AFTER_SECONDS,
) -> dict[str, object]:
    """Internal helper."""
    task_center = service or _task_center_service()
    if workitem is None:
        workitem = next((item for item in state.workitems if item.id == assignment.workitem_id), None)
    if artifacts is None:
        artifacts = [
            {
                "id": artifact.id,
                "kind": artifact.kind,
                "title": artifact.title,
                "path": artifact.path or "",
                "source_backend": artifact.source_backend,
                "version": artifact.version,
            }
            for artifact in state.artifacts
            if artifact.workitem_id == assignment.workitem_id
        ]
    claimed_age_seconds = task_center.claimed_age_seconds(assignment)
    heartbeat_age_seconds = task_center.heartbeat_age_seconds(assignment)
    write_scope_conflicts = task_center.write_scope_conflicts(state, assignment)
    return {
        "id": assignment.id,
        "workitem_id": assignment.workitem_id,
        "role": assignment.role,
        "status": assignment.status.value,
        "assigned_agent_id": assignment.assigned_agent_id or "",
        "claim_token": assignment.claim_token,
        "claim_reason": assignment.claim_reason,
        "claimable": task_center.claimable(state, assignment),
        "unmet_dependency_ids": task_center.unmet_dependency_ids(state, assignment),
        "write_scope_conflict_assignment_ids": write_scope_conflicts,
        "claimed_age_seconds": claimed_age_seconds,
        "heartbeat_age_seconds": heartbeat_age_seconds,
        "stale_claimed": task_center.stale_claimed(assignment, stale_after_seconds=stale_after_seconds),
        "dependencies": list(assignment.dependencies),
        "input_artifact_ids": list(assignment.input_artifact_ids),
        "output_artifact_ids": list(assignment.output_artifact_ids),
        "result_summary": assignment.result_summary,
        "blocked_reason": assignment.blocked_reason or "",
        "claimed_at": assignment.claimed_at,
        "last_heartbeat_at": assignment.last_heartbeat_at,
        "lease_seconds": assignment.lease_seconds,
        "lease_expires_at": assignment.lease_expires_at,
        "lease_expired": task_center.lease_expired(assignment),
        "returned_at": assignment.returned_at,
        "prompt_file": assignment.prompt_file,
        "return_commands": build_task_return_commands(state.project.project_root, assignment),
        "return_api_paths": _task_return_api_paths(state, assignment),
        "workitem": _task_workitem_payload(workitem),
        "artifacts": artifacts,
    }


def _task_return_api_paths(state: SharedProjectState, assignment: TaskAssignment) -> dict[str, str]:
    base = f"/api/projects/{state.project.id}/tasks/{assignment.id}"
    if assignment.status == TaskAssignmentStatus.FAILED:
        return {
            "release": f"{base}/release",
        }
    if assignment.status != TaskAssignmentStatus.CLAIMED:
        return {}
    return {
        "complete": f"{base}/complete",
        "fail": f"{base}/fail",
        "heartbeat": f"{base}/heartbeat",
        "release": f"{base}/release",
    }


def _record_task_prompt_file(state: SharedProjectState, assignment_id: str, prompt_file: Path) -> None:
    assignment = next((item for item in state.task_assignments if item.id == assignment_id), None)
    if assignment is None:
        raise HTTPException(status_code=404, detail=f"Task assignment not found: {assignment_id}")
    engine.state_store.upsert_task_assignment(state.project.id, replace(assignment, prompt_file=str(prompt_file)))


def _validate_task_prompt_file_request(project_id: str, prompt_file: str) -> None:
    if not prompt_file:
        return
    state = _require_project_state(project_id)
    try:
        resolve_task_prompt_path(prompt_file, state.project.project_root)
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error


def _task_center_service() -> TaskCenterService:
    """Internal helper."""
    return TaskCenterService(engine.state_store, event_prefix="TaskCenter")


def _run_task_center_transition(action: Callable, *args, **kwargs):
    """Internal helper."""
    try:
        return action(*args, **kwargs)
    except TaskCenterError as error:
        raise HTTPException(status_code=error.status_code, detail=str(error)) from error


def _task_workitem_payload(workitem) -> dict[str, object]:
    """Internal helper."""
    if workitem is None:
        return {}
    return {
        "id": workitem.id,
        "stage": workitem.stage,
        "kind": workitem.kind,
        "status": workitem.status.value,
        "owner_agent": workitem.owner_agent or "",
        "description": workitem.description,
        "acceptance_criteria": list(workitem.acceptance_criteria),
        "retry_count": workitem.retry_count,
        "max_retries": workitem.max_retries,
        "failure_type": workitem.failure_type,
        "failure_summary": workitem.failure_summary,
    }


@app.post("/api/projects/{project_id}/step")
def step_project_api(project_id: str) -> JSONResponse:
    """Internal helper."""
    block_reason = _project_run_block_reason(project_id)
    if block_reason:
        return _blocked_project_task_response(project_id, "step", "step", block_reason)
    started = start_project_task(project_id, "step", "step", engine.step_project)
    return JSONResponse(
        {
            "project_id": project_id,
            "accepted": started,
            "task_status": _task_status_payload(project_id),
        },
        status_code=202 if started else 409,
    )


@app.post("/api/projects/{project_id}/run")
def run_project_api(project_id: str) -> JSONResponse:
    """Internal helper."""
    block_reason = _project_run_block_reason(project_id)
    if block_reason:
        return _blocked_project_task_response(project_id, "run", "run", block_reason)
    started = start_project_task(project_id, "run", "run", engine.run_project)
    return JSONResponse(
        {
            "project_id": project_id,
            "accepted": started,
            "task_status": _task_status_payload(project_id),
        },
        status_code=202 if started else 409,
    )


@app.get("/api/projects/{project_id}/live")
def project_live_api(project_id: str) -> JSONResponse:
    """Internal helper."""
    return project_live_state(project_id)


@app.get("/api/settings/execution")
def execution_settings_api() -> JSONResponse:
    """Internal helper."""
    config = load_execution_scope_config()
    return JSONResponse(asdict(config))


@app.post("/api/settings/execution")
async def save_execution_settings_api(request: Request) -> JSONResponse:
    """Internal helper."""
    payload = await request.json()
    config = ExecutionScopeConfig(
        run_profile=_normalize_run_profile(payload.get("run_profile", RunProfile.MOCK.value)),
        requirement_design_enabled=bool(payload.get("requirement_design_enabled", True)),
        design_detail_enabled=bool(payload.get("design_detail_enabled", True)),
        design_collaboration_enabled=bool(payload.get("design_collaboration_enabled", True)),
        backend_development_enabled=bool(payload.get("backend_development_enabled", True)),
        testing_enabled=bool(payload.get("testing_enabled", True)),
    )
    if not _execution_scope_has_enabled_stage(config):
        raise HTTPException(status_code=400, detail="At least one execution scope stage must be enabled.")
    save_execution_scope_config(config)
    refresh_engine_execution_scope()
    return JSONResponse({"saved": True, "config": asdict(config)})


@app.get("/api/settings/cli")
def cli_settings_api() -> JSONResponse:
    """Internal helper."""
    config = load_cli_selection_config()
    return JSONResponse(
        {
            "config": asdict(config),
            "cli_options": build_cli_options(config),
            "role_cli_options": build_role_cli_binding_options(config),
        }
    )


@app.post("/api/settings/cli")
async def save_cli_settings_api(request: Request) -> JSONResponse:
    """Internal helper."""
    payload = await request.json()
    config = CLISelectionConfig(
        selected_cli_names=list(payload.get("selected_cli_names", [])),
        role_cli_bindings=dict(payload.get("role_cli_bindings", {})),
        codex_model=str(payload.get("codex_model", "gpt-5.4-mini")),
        codex_reasoning_effort=str(payload.get("codex_reasoning_effort", "medium")),
    )
    config = _sanitize_cli_selection_config(config)
    save_cli_selection_config(config)
    refresh_engine_cli_config()
    return JSONResponse({"saved": True, "config": asdict(config)})


@app.get("/api/settings/llm")
def llm_settings_api() -> JSONResponse:
    """Internal helper."""
    config = load_llm_runtime_config()
    return JSONResponse(llm_settings_payload(config))


@app.get("/api/diagnostics")
def diagnostics_api(probe_cli: bool = False, probe_llm: bool = False, preflight_llm: bool = False) -> JSONResponse:
    """Internal helper."""
    llm_runtime_config = load_llm_runtime_config()
    diagnostics = build_platform_diagnostics(
        cli_config=load_cli_selection_config(),
        project_root=ROOT_DIR,
        llm_runtime_config=llm_runtime_config,
        probe_cli=probe_cli,
        probe_llm=probe_llm or preflight_llm,
        preflight_probe=(
            build_requirement_llm_preflight_probe(
                llm_runtime_config,
                ROOT_DIR / ".conductor" / "diagnostics",
            )
            if preflight_llm
            else None
        ),
    )
    return JSONResponse(diagnostics.to_dict())


@app.post("/api/settings/llm")
async def save_llm_settings_api(request: Request) -> JSONResponse:
    """Internal helper."""
    payload = await request.json()
    existing_config = load_llm_runtime_config()
    config = llm_config_from_settings_payload(payload, existing_config)
    save_llm_runtime_config(config)
    refresh_engine_llm_backend()
    return JSONResponse({"saved": True, "config": llm_settings_payload(config)})


@app.post("/api/settings/llm/preflight")
async def llm_settings_preflight_api(request: Request) -> JSONResponse:
    """Internal helper."""
    payload = await request.json()
    backend = str(payload.get("backend", "cloud"))
    if backend not in {"local", "cloud"}:
        raise HTTPException(status_code=422, detail="backend must be local or cloud")
    existing_config = load_llm_runtime_config()
    config = llm_config_from_settings_payload(payload, existing_config)
    llm_config = config.local if backend == "local" else config.cloud
    llm_config.enabled = True
    result = run_requirement_llm_preflight(
        backend=backend,
        config=llm_config,
        output_dir=ROOT_DIR / ".conductor" / "diagnostics" / "settings-llm-preflight",
    )
    return JSONResponse(asdict(result), status_code=200 if result.success else 502)


@app.get("/projects/{project_id}/runtime/stream")
def project_runtime_stream(project_id: str) -> StreamingResponse:
    """Internal helper."""
    _require_project_state(project_id)

    def event_stream():
        last_version = -1
        while True:
            snapshot = engine.runtime_stream_store.wait_for_update(project_id, last_version, timeout_seconds=1.0)
            if snapshot.version <= last_version:
                yield ": ping\n\n"
                continue
            payload = {
                "project_id": snapshot.project_id,
                "running": snapshot.running,
                "status": snapshot.status,
                "workitem_id": snapshot.workitem_id,
                "agent_role": snapshot.agent_role,
                "backend": snapshot.backend,
                "cli_name": snapshot.cli_name,
                "lines": snapshot.lines,
                "version": snapshot.version,
            }
            last_version = snapshot.version
            yield f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"

    return StreamingResponse(event_stream(), media_type="text/event-stream")




@app.get("/api/todos")
def list_todos_api(
    request: Request,
    status: TodoListStatus = "all",
    query: str | None = None,
    q: str | None = None,
) -> JSONResponse:
    """Internal helper."""
    effective_query = q if q is not None else query
    todos = [item.to_dict() for item in get_todo_service(request).list_todos(status, effective_query)]
    return JSONResponse({"todos": todos})


@app.get("/api/todos/stats")
def todo_stats_api(request: Request) -> JSONResponse:
    """Internal helper."""
    return JSONResponse(get_todo_service(request).get_stats())


@app.post("/api/todos", status_code=201)
def create_todo_api(request: Request, payload: TodoCreateRequest) -> JSONResponse:
    """Internal helper."""
    try:
        todo = get_todo_service(request).create_todo(payload.title, payload.completed, payload.content)
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error
    return JSONResponse({"todo": todo.to_dict()}, status_code=201)


@app.get("/api/todos/{todo_id}")
def get_todo_api(request: Request, todo_id: str) -> JSONResponse:
    """Internal helper."""
    todo = get_todo_service(request).get_todo(todo_id)
    if todo is None:
        raise HTTPException(status_code=404, detail="todo not found")
    return JSONResponse({"todo": todo.to_dict()})


@app.patch("/api/todos/{todo_id}")
def update_todo_api(request: Request, todo_id: str, payload: TodoUpdateRequest) -> JSONResponse:
    """Internal helper."""
    try:
        todo = get_todo_service(request).update_todo(
            todo_id,
            title=payload.title,
            completed=payload.completed,
            content=payload.content,
        )
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error
    if todo is None:
        raise HTTPException(status_code=404, detail="todo not found")
    return JSONResponse({"todo": todo.to_dict()})


@app.delete("/api/todos/{todo_id}")
def delete_todo_api(request: Request, todo_id: str) -> JSONResponse:
    """Internal helper."""
    deleted = get_todo_service(request).delete_todo(todo_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="todo not found")
    return JSONResponse({"deleted": True})


def _form_value(form: dict[str, list[str]], name: str, default: str) -> str:
    """Internal helper."""
    values = form.get(name)
    if not values or values[0] == "":
        return default
    return values[0]


def _optional_form_value(form: dict[str, list[str]], name: str) -> str | None:
    """Internal helper."""
    value = _form_value(form, name, "")
    return value or None


def _form_checked(form: dict[str, list[str]], name: str) -> bool:
    """Internal helper."""
    return name in form
