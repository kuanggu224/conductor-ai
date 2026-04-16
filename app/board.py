"""Board Web 入口。"""

from __future__ import annotations

import json
import sys
from dataclasses import dataclass
from pathlib import Path
from threading import Lock, Thread
from typing import Callable
from urllib.parse import parse_qs

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, StreamingResponse
from fastapi.templating import Jinja2Templates

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
    load_execution_scope_config,
    save_execution_scope_config,
)
from conductor.config.llm import (
    LLMUsagePolicy,
    LLMRuntimeConfig,
    build_default_hybrid_llm_backend,
    load_llm_runtime_config,
    save_llm_runtime_config,
)
from conductor.controller.engine import ConductorEngine

app = FastAPI(title="Conductor Board")
templates = Jinja2Templates(directory=str(ROOT_DIR / "templates"))
board_service = BoardService()
engine = ConductorEngine()


@dataclass(slots=True)
class ProjectTaskStatus:
    """Board 后台任务状态。"""

    running: bool = False
    action: str = ""
    action_label: str = ""
    message: str = "空闲"
    error: str | None = None


task_statuses: dict[str, ProjectTaskStatus] = {}
task_lock = Lock()


def refresh_engine_llm_backend() -> None:
    """热更新当前 engine 中所有 Agent 的 LLM backend。"""
    config = load_llm_runtime_config()
    llm_backend = build_default_hybrid_llm_backend(config)
    engine.llm_runtime_config = config
    engine.runner.llm_usage_policy = config.usage
    engine.registry.llm_backend = llm_backend
    for agent in engine.registry.agents:
        agent.llm_backend = llm_backend


def refresh_engine_execution_scope() -> None:
    """热更新当前 engine 的执行范围配置。"""
    config = load_execution_scope_config()
    engine.execution_scope_config = config
    engine.planner.scope_config = config
    engine.collaboration_runner.policy.enabled = config.design_collaboration_enabled


def refresh_engine_cli_config() -> None:
    """热更新当前 engine 的 Agent CLI 选择配置。"""
    config = load_cli_selection_config()
    engine.cli_selection_config = config
    engine.runner.cli_selection_config = config
    engine.runner.agent_cli_executor.cli_selection_config = config
    engine.collaboration_runner.cli_selection_config = config
    engine.collaboration_runner.agent_cli_executor.cli_selection_config = config


def get_project_task_status(project_id: str) -> ProjectTaskStatus:
    """读取项目后台任务状态。"""
    with task_lock:
        return task_statuses.get(project_id, ProjectTaskStatus())


def start_project_task(project_id: str, action: str, action_label: str, target: Callable[[str], object]) -> bool:
    """启动项目后台任务；同一项目已有任务时不重复启动。"""
    with task_lock:
        current = task_statuses.get(project_id)
        if current and current.running:
            return False
        task_statuses[project_id] = ProjectTaskStatus(
            running=True,
            action=action,
            action_label=action_label,
            message=f"{action_label} 已开始，页面会自动刷新状态。",
        )
    Thread(
        target=run_project_task,
        args=(project_id, action, action_label, target),
        daemon=True,
    ).start()
    return True


def run_project_task(project_id: str, action: str, action_label: str, target: Callable[[str], object]) -> None:
    """执行项目后台任务并记录状态。"""
    try:
        target(project_id)
    except Exception as error:
        with task_lock:
            task_statuses[project_id] = ProjectTaskStatus(
                running=False,
                action=action,
                action_label=action_label,
                message=f"{action_label} 失败。",
                error=str(error),
            )
        return
    with task_lock:
        task_statuses[project_id] = ProjectTaskStatus(
            running=False,
            action=action,
            action_label=action_label,
            message=f"{action_label} 已完成。",
        )


@app.get("/", response_class=HTMLResponse)
def board_page(request: Request) -> HTMLResponse:
    """渲染项目列表和默认页面。"""
    requirement = request.query_params.get("requirement", "")
    project_root = request.query_params.get("project_root", str(ROOT_DIR))
    states = engine.list_projects()
    project_summaries = board_service.build_project_summaries(states)
    selected_state = states[0] if states else None
    snapshot = board_service.build_snapshot(
        selected_state,
        cli_config=engine.cli_selection_config,
        llm_runtime_config=engine.llm_runtime_config,
    ) if selected_state else None
    return templates.TemplateResponse(
        request=request,
        name="board.html",
        context={
            "snapshot": snapshot,
            "available_roles": engine.registry.list_roles(),
            "available_role_labels": board_service.build_role_labels(engine.registry.list_roles()),
            "requirement": requirement,
            "project_root": project_root,
            "project_summaries": project_summaries,
            "task_status": get_project_task_status(snapshot.project_id) if snapshot else None,
        },
    )


@app.get("/projects/create")
def create_project(requirement: str, project_root: str | None = None) -> RedirectResponse:
    """创建真实项目实例并跳转到详情页。"""
    state = engine.create_project(requirement=requirement, project_root=project_root)
    return RedirectResponse(url=f"/projects/{state.project.id}", status_code=303)


@app.get("/folders/pick", response_class=HTMLResponse)
def folder_picker_page(
    request: Request,
    current_path: str | None = None,
    requirement: str | None = None,
) -> HTMLResponse:
    """Render a filesystem folder picker page."""
    path = Path(current_path or ROOT_DIR).expanduser().resolve()
    if not path.exists():
        path = ROOT_DIR
    if path.is_file():
        path = path.parent
    children = []
    try:
        children = sorted(
            [item for item in path.iterdir() if item.is_dir()],
            key=lambda item: item.name.lower(),
        )
    except OSError:
        children = []
    parent_path = str(path.parent) if path.parent != path else None
    return templates.TemplateResponse(
        request=request,
        name="folder_picker.html",
        context={
            "current_path": str(path),
            "parent_path": parent_path,
            "children": [str(item) for item in children[:200]],
            "requirement": requirement or "",
        },
    )


@app.get("/projects/{project_id}", response_class=HTMLResponse)
def board_project_page(request: Request, project_id: str) -> HTMLResponse:
    """渲染指定项目详情。"""
    state = engine.get_project(project_id)
    snapshot = board_service.build_snapshot(
        state,
        cli_config=engine.cli_selection_config,
        llm_runtime_config=engine.llm_runtime_config,
    )


    return templates.TemplateResponse(
        request=request,
        name="board.html",
        context={
            "snapshot": snapshot,
            "available_roles": engine.registry.list_roles(),
            "available_role_labels": board_service.build_role_labels(engine.registry.list_roles()),
            "requirement": state.project.goal,
            "project_root": state.project.project_root,
            "project_summaries": board_service.build_project_summaries(engine.list_projects()),
            "task_status": get_project_task_status(project_id),
        },
    )


@app.get("/projects/{project_id}/live")
def project_live_state(project_id: str) -> JSONResponse:
    """Return lightweight live state for partial board updates."""
    state = engine.get_project(project_id)
    snapshot = board_service.build_snapshot(
        state,
        cli_config=engine.cli_selection_config,
        llm_runtime_config=engine.llm_runtime_config,
    )
    task_status = get_project_task_status(project_id)
    return JSONResponse(
        {
            "project_id": snapshot.project_id,
            "project_status_label": snapshot.project_status_label,
            "current_stage_label": snapshot.current_stage_label,
            "task_status": {
                "running": task_status.running,
                "action": task_status.action,
                "action_label": task_status.action_label,
                "message": task_status.message,
                "error": task_status.error,
            },
            "execution_runtime": {
                "available": snapshot.execution_runtime.available,
                "is_running": snapshot.execution_runtime.is_running,
                "headline": snapshot.execution_runtime.headline,
                "workitem_id": snapshot.execution_runtime.workitem_id,
                "workitem_kind_label": snapshot.execution_runtime.workitem_kind_label,
                "stage_label": snapshot.execution_runtime.stage_label,
                "agent_label": snapshot.execution_runtime.agent_label,
                "backend_label": snapshot.execution_runtime.backend_label,
                "cli_label": snapshot.execution_runtime.cli_label,
                "model_label": snapshot.execution_runtime.model_label,
                "working_directory": snapshot.execution_runtime.working_directory,
                "execution_mode_label": snapshot.execution_runtime.execution_mode_label,
                "state_label": snapshot.execution_runtime.state_label,
                "stage_progress_label": snapshot.execution_runtime.stage_progress_label,
                "stage_progress_percent": snapshot.execution_runtime.stage_progress_percent,
                "task_position_label": snapshot.execution_runtime.task_position_label,
                "output_summary_title": snapshot.execution_runtime.output_summary_title,
                "output_summary": snapshot.execution_runtime.output_summary,
            },
            "recent_events_tail": snapshot.recent_events_tail,
        }
    )


@app.get("/projects/{project_id}/runtime/stream")
def project_runtime_stream(project_id: str) -> StreamingResponse:
    """Stream live CLI output for the current project."""

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


@app.get("/settings/execution", response_class=HTMLResponse)
def execution_settings_page(request: Request, saved: str | None = None) -> HTMLResponse:
    """渲染执行范围配置页面。"""
    return templates.TemplateResponse(
        request=request,
        name="execution_config.html",
        context={
            "config": load_execution_scope_config(),
            "saved": saved == "1",
        },
    )


@app.get("/settings/cli", response_class=HTMLResponse)
def cli_settings_page(request: Request, saved: str | None = None) -> HTMLResponse:
    """渲染 Agent CLI 配置页面。"""
    return templates.TemplateResponse(
        request=request,
        name="cli_config.html",
        context={
            "config": load_cli_selection_config(),
            "cli_options": build_cli_options(),
            "role_cli_options": build_role_cli_binding_options(),
            "saved": saved == "1",
        },
    )


@app.post("/settings/cli")
async def save_cli_settings(request: Request) -> RedirectResponse:
    """保存 Agent CLI 选择配置并热更新 engine。"""
    body = (await request.body()).decode("utf-8")
    form = parse_qs(body)
    role_bindings = {}
    for role in engine.registry.list_roles():
        values = form.get(f"role_cli_binding_{role}", [""])
        role_bindings[role] = values[0] or None
    config = CLISelectionConfig(
        selected_cli_names=form.get("selected_cli_names", []),
        role_cli_bindings=role_bindings,
        codex_model=_form_value(form, "codex_model", "gpt-5.4-mini"),
        codex_reasoning_effort=_form_value(form, "codex_reasoning_effort", "medium"),
    )
    save_cli_selection_config(config)
    refresh_engine_cli_config()
    return RedirectResponse(url="/settings/cli?saved=1", status_code=303)


@app.post("/settings/execution")
async def save_execution_settings(request: Request) -> RedirectResponse:
    """保存执行范围配置并热更新新项目规划策略。"""
    body = (await request.body()).decode("utf-8")
    form = parse_qs(body)
    config = ExecutionScopeConfig(
        requirement_design_enabled=_form_checked(form, "requirement_design_enabled"),
        design_detail_enabled=_form_checked(form, "design_detail_enabled"),
        design_collaboration_enabled=_form_checked(form, "design_collaboration_enabled"),
        backend_development_enabled=_form_checked(form, "backend_development_enabled"),
        frontend_development_enabled=_form_checked(form, "frontend_development_enabled"),
        testing_enabled=_form_checked(form, "testing_enabled"),
    )
    save_execution_scope_config(config)
    refresh_engine_execution_scope()
    return RedirectResponse(url="/settings/execution?saved=1", status_code=303)


@app.get("/projects/{project_id}/step")
def step_project(project_id: str) -> RedirectResponse:
    """异步推进一步。"""
    start_project_task(project_id, "step", "单步推进", engine.step_project)
    return RedirectResponse(url=f"/projects/{project_id}", status_code=303)


@app.get("/projects/{project_id}/run")
def run_project(project_id: str) -> RedirectResponse:
    """异步持续推进到终态。"""
    start_project_task(project_id, "run", "运行到终态", engine.run_project)
    return RedirectResponse(url=f"/projects/{project_id}", status_code=303)


@app.get("/settings/llm", response_class=HTMLResponse)
def llm_settings_page(request: Request, saved: str | None = None) -> HTMLResponse:
    """渲染 LLM 配置页面。"""
    return templates.TemplateResponse(
        request=request,
        name="llm_config.html",
        context={
            "config": load_llm_runtime_config(),
            "saved": saved == "1",
        },
    )


@app.post("/settings/llm")
async def save_llm_settings(request: Request) -> RedirectResponse:
    """保存 LLM 配置并热更新 Agent backend。"""
    body = (await request.body()).decode("utf-8")
    form = parse_qs(body)
    config = LLMRuntimeConfig(
        local=LLMHTTPConfig(
            base_url=_form_value(form, "local_base_url", "http://127.0.0.1:11434/v1"),
            model_name=_form_value(form, "local_model", "local-demo-model"),
            api_key=_optional_form_value(form, "local_api_key"),
            timeout_seconds=float(_form_value(form, "local_timeout", "30.0")),
            enabled=_form_checked(form, "local_enabled"),
        ),
        cloud=LLMHTTPConfig(
            base_url=_form_value(form, "cloud_base_url", "https://api.openai.com/v1"),
            model_name=_form_value(form, "cloud_model", "gpt-demo-model"),
            api_key=_optional_form_value(form, "cloud_api_key"),
            timeout_seconds=float(_form_value(form, "cloud_timeout", "30.0")),
            enabled=_form_checked(form, "cloud_enabled"),
        ),
        usage=LLMUsagePolicy(
            runner_enabled=_form_checked(form, "runner_llm_enabled"),
            runner_allowed_roles=["designer", "backend_engineer", "frontend_engineer", "tester"],
            runner_allowed_kinds=[
                "design_overview",
                "ui_design",
                "api_design",
                "test_design",
                "generic_implementation",
                "api_implementation",
                "data_implementation",
                "ui_implementation",
                "acceptance_check",
                "automated_test",
                "api_validation",
                "ui_validation",
            ],
            preferred_backend=_form_value(form, "runner_preferred_backend", "cloud"),
        ),
    )
    save_llm_runtime_config(config)
    refresh_engine_llm_backend()
    return RedirectResponse(url="/settings/llm?saved=1", status_code=303)


def _form_value(form: dict[str, list[str]], name: str, default: str) -> str:
    """读取表单字符串值。"""
    values = form.get(name)
    if not values or values[0] == "":
        return default
    return values[0]


def _optional_form_value(form: dict[str, list[str]], name: str) -> str | None:
    """读取可空表单字符串值。"""
    value = _form_value(form, name, "")
    return value or None


def _form_checked(form: dict[str, list[str]], name: str) -> bool:
    """读取 checkbox 值。"""
    return name in form
