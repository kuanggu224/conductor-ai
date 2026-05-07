"""Board Web 入口。"""

from __future__ import annotations

import json
import sys
from dataclasses import asdict, dataclass, replace
from contextlib import asynccontextmanager
from pathlib import Path
from threading import Lock, Thread
from typing import Annotated, Callable
from urllib.parse import parse_qs

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, StreamingResponse
from fastapi.middleware.cors import CORSMiddleware
from fastapi.templating import Jinja2Templates
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
from conductor.domain.models import SharedProjectState, TaskAssignment, TaskAssignmentStatus, WorkItemStatus
from conductor.io.encoding import configure_utf8_stdio
from conductor.io.requirements import RequirementInputError, load_requirement_text
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


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Create a fresh in-memory todo store for the app lifecycle."""
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
templates = Jinja2Templates(directory=str(ROOT_DIR / "templates"))
board_service = BoardService()
engine = ConductorEngine()


class TodoCreateRequest(BaseModel):
    """Payload for creating a to-do item."""

    title: TodoTitle
    content: TodoContent = ""
    completed: StrictBool = False


class TodoUpdateRequest(BaseModel):
    """Payload for updating a to-do item."""

    title: OptionalTodoTitle = None
    completed: StrictBool | None = None
    content: OptionalTodoContent = None


class TaskClaimRequest(BaseModel):
    """Payload for claiming a task-center assignment."""

    agent_id: TodoTitle
    claim_reason: TodoContent = ""


class TaskClaimNextRequest(TaskClaimRequest):
    """Payload for claiming the next available task-center assignment."""

    role: OptionalTodoTitle = None


class TaskReturnRequest(BaseModel):
    """Payload for returning a task-center assignment."""

    result_summary: TodoContent = ""
    output_artifact_ids: list[str] = Field(default_factory=list)
    blocked_reason: TodoContent = ""


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


def get_todo_service(request: Request | None = None) -> TodoService:
    """Return the active todo service, creating it if necessary."""
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
    """Create and attach the in-memory store when it is missing."""
    service = create_todo_service()
    ensure_todo_registry(app.state)["default"] = service
    return service


@app.middleware("http")
async def todo_session_middleware(request: Request, call_next):
    """Bind each client session to its own in-memory todo store."""
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


def _task_status_payload(project_id: str) -> dict[str, object]:
    """Build a serializable task status payload."""
    status = get_project_task_status(project_id)
    return {
        "running": status.running,
        "action": status.action,
        "action_label": status.action_label,
        "message": status.message,
        "error": status.error,
    }


def _snapshot_payload(project_id: str):
    """Build a serializable board snapshot payload."""
    state = _require_project_state(project_id)
    snapshot = board_service.build_snapshot(
        state,
        cli_config=engine.cli_selection_config,
        llm_runtime_config=engine.llm_runtime_config,
    )
    return snapshot, asdict(snapshot)


def _require_project_state(project_id: str):
    """Return an existing project state or raise a 404 for API callers."""
    try:
        return engine.get_project(project_id)
    except KeyError as error:
        raise HTTPException(status_code=404, detail=f"Project not found: {project_id}") from error


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
def create_project(
    requirement: str = "",
    project_root: str | None = None,
    requirement_file: str | None = None,
    requirement_json_file: str | None = None,
    requirement_json_key: str = "requirement",
) -> RedirectResponse:
    """创建真实项目实例并跳转到详情页。"""
    try:
        requirement = load_requirement_text(
            requirement=requirement,
            requirement_file=requirement_file,
            requirement_json_file=requirement_json_file,
            json_key=requirement_json_key,
        )
    except RequirementInputError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error
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
    state = _require_project_state(project_id)
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
    snapshot, _ = _snapshot_payload(project_id)
    task_status = _task_status_payload(project_id)
    return JSONResponse(
        {
            "project_id": snapshot.project_id,
            "project_status_label": snapshot.project_status_label,
            "current_stage_label": snapshot.current_stage_label,
            "task_status": task_status,
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


@app.get("/api/projects")
def list_projects_api() -> JSONResponse:
    """Return all projects for the standalone frontend."""
    states = engine.list_projects()
    payload = {
        "projects": [asdict(item) for item in board_service.build_project_summaries(states)],
    }
    return JSONResponse(payload)


@app.post("/api/projects")
async def create_project_api(request: Request) -> JSONResponse:
    """Create a project from JSON payload."""
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
    """Return one project snapshot."""
    snapshot, snapshot_payload = _snapshot_payload(project_id)
    return JSONResponse(
        {
            "snapshot": snapshot_payload,
            "task_status": _task_status_payload(snapshot.project_id),
        }
    )


@app.get("/api/projects/{project_id}/tasks")
def project_tasks_api(project_id: str, status: str | None = None) -> JSONResponse:
    """Return task center assignments for one project, optionally filtered by status."""
    state = _require_project_state(project_id)
    return JSONResponse(_task_center_payload(state, status=status))


@app.post("/api/projects/{project_id}/tasks/claim-next")
async def claim_next_project_task_api(project_id: str, payload: TaskClaimNextRequest) -> JSONResponse:
    """Claim the next queued task-center assignment, optionally filtered by role."""
    state = _require_project_state(project_id)
    assignment = _select_next_task_assignment(state, role=payload.role)
    updated = _claim_task_assignment(project_id, assignment, payload.agent_id, payload.claim_reason)
    state = _require_project_state(project_id)
    return JSONResponse(
        {
            "project_id": project_id,
            "task": _task_assignment_payload(updated, state),
        }
    )


@app.post("/api/projects/{project_id}/tasks/{assignment_id}/claim")
async def claim_project_task_api(project_id: str, assignment_id: str, payload: TaskClaimRequest) -> JSONResponse:
    """Claim one queued task-center assignment."""
    state = _require_project_state(project_id)
    assignment = _require_task_assignment(state, assignment_id)
    updated = _claim_task_assignment(project_id, assignment, payload.agent_id, payload.claim_reason)
    state = _require_project_state(project_id)
    return JSONResponse(
        {
            "project_id": project_id,
            "task": _task_assignment_payload(updated, state),
        }
    )


@app.post("/api/projects/{project_id}/tasks/{assignment_id}/complete")
async def complete_project_task_api(project_id: str, assignment_id: str, payload: TaskReturnRequest) -> JSONResponse:
    """Return a claimed task-center assignment as completed."""
    state = _require_project_state(project_id)
    assignment = _require_task_assignment(state, assignment_id)
    if assignment.status != TaskAssignmentStatus.CLAIMED:
        raise HTTPException(
            status_code=409,
            detail=f"Task assignment is not claimed: {assignment.status.value}",
        )
    updated = replace(
        assignment,
        status=TaskAssignmentStatus.COMPLETED,
        output_artifact_ids=list(payload.output_artifact_ids),
        result_summary=payload.result_summary,
        blocked_reason=None,
    )
    _sync_task_workitem_return(project_id, assignment, WorkItemStatus.DONE, payload)
    engine.state_store.upsert_task_assignment(project_id, updated)
    engine.state_store.add_event(project_id, f"TaskCenter: {assignment.workitem_id} returned completed")
    state = _require_project_state(project_id)
    return JSONResponse(
        {
            "project_id": project_id,
            "task": _task_assignment_payload(updated, state),
        }
    )


@app.post("/api/projects/{project_id}/tasks/{assignment_id}/fail")
async def fail_project_task_api(project_id: str, assignment_id: str, payload: TaskReturnRequest) -> JSONResponse:
    """Return a claimed task-center assignment as failed."""
    state = _require_project_state(project_id)
    assignment = _require_task_assignment(state, assignment_id)
    if assignment.status != TaskAssignmentStatus.CLAIMED:
        raise HTTPException(
            status_code=409,
            detail=f"Task assignment is not claimed: {assignment.status.value}",
        )
    updated = replace(
        assignment,
        status=TaskAssignmentStatus.FAILED,
        output_artifact_ids=list(payload.output_artifact_ids),
        result_summary=payload.result_summary,
        blocked_reason=payload.blocked_reason or None,
    )
    _sync_task_workitem_return(project_id, assignment, WorkItemStatus.FAILED, payload)
    engine.state_store.upsert_task_assignment(project_id, updated)
    engine.state_store.add_event(project_id, f"TaskCenter: {assignment.workitem_id} returned failed")
    state = _require_project_state(project_id)
    return JSONResponse(
        {
            "project_id": project_id,
            "task": _task_assignment_payload(updated, state),
        }
    )


def _task_center_payload(state: SharedProjectState, status: str | None = None) -> dict[str, object]:
    """Build the task-center response payload."""
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
    return {
        "project_id": state.project.id,
        "status_filter": status or "",
        "total": len(assignments),
        "tasks": [
            _task_assignment_payload(
                assignment,
                state,
                workitem=workitems_by_id.get(assignment.workitem_id),
                artifacts=artifacts_by_workitem.get(assignment.workitem_id, []),
            )
            for assignment in assignments
        ],
    }


def _task_assignment_payload(
    assignment: TaskAssignment,
    state: SharedProjectState,
    workitem=None,
    artifacts: list[dict[str, object]] | None = None,
) -> dict[str, object]:
    """Build one task-center assignment payload."""
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
    return {
        "id": assignment.id,
        "workitem_id": assignment.workitem_id,
        "role": assignment.role,
        "status": assignment.status.value,
        "assigned_agent_id": assignment.assigned_agent_id or "",
        "claim_reason": assignment.claim_reason,
        "dependencies": list(assignment.dependencies),
        "input_artifact_ids": list(assignment.input_artifact_ids),
        "output_artifact_ids": list(assignment.output_artifact_ids),
        "result_summary": assignment.result_summary,
        "blocked_reason": assignment.blocked_reason or "",
        "workitem": _task_workitem_payload(workitem),
        "artifacts": artifacts,
    }


def _require_task_assignment(state: SharedProjectState, assignment_id: str) -> TaskAssignment:
    """Return an assignment or raise 404."""
    for assignment in state.task_assignments:
        if assignment.id == assignment_id:
            return assignment
    raise HTTPException(status_code=404, detail=f"Task assignment not found: {assignment_id}")


def _claim_task_assignment(
    project_id: str,
    assignment: TaskAssignment,
    agent_id: str,
    claim_reason: str = "",
) -> TaskAssignment:
    """Claim one queued task-center assignment and sync its WorkItem."""
    if assignment.status != TaskAssignmentStatus.QUEUED:
        raise HTTPException(
            status_code=409,
            detail=f"Task assignment is not queued: {assignment.status.value}",
        )
    updated = replace(
        assignment,
        status=TaskAssignmentStatus.CLAIMED,
        assigned_agent_id=agent_id,
        claim_reason=claim_reason,
        blocked_reason=None,
    )
    _sync_task_workitem_claim(project_id, assignment, agent_id)
    engine.state_store.upsert_task_assignment(project_id, updated)
    engine.state_store.add_event(project_id, f"TaskCenter: {agent_id} claimed {assignment.workitem_id}")
    return updated


def _select_next_task_assignment(state: SharedProjectState, role: str | None = None) -> TaskAssignment:
    """Return the first queued assignment whose dependencies are satisfied."""
    for assignment in state.task_assignments:
        if assignment.status != TaskAssignmentStatus.QUEUED:
            continue
        if role and assignment.role != role:
            continue
        if not _task_assignment_dependencies_satisfied(state, assignment):
            continue
        return assignment
    suffix = f" for role {role}" if role else ""
    raise HTTPException(status_code=404, detail=f"No queued task assignment available{suffix}.")


def _task_assignment_dependencies_satisfied(state: SharedProjectState, assignment: TaskAssignment) -> bool:
    """Return whether all WorkItem dependencies for an assignment are done."""
    if not assignment.dependencies:
        return True
    status_by_workitem = {item.id: item.status for item in state.workitems}
    return all(status_by_workitem.get(dependency_id) == WorkItemStatus.DONE for dependency_id in assignment.dependencies)


def _sync_task_workitem_claim(project_id: str, assignment: TaskAssignment, agent_id: str) -> None:
    """Keep WorkItem lifecycle aligned with manual task-center claims."""
    try:
        engine.state_store.update_workitem(
            project_id,
            assignment.workitem_id,
            WorkItemStatus.RUNNING,
            owner_agent=agent_id,
        )
    except KeyError as error:
        raise HTTPException(status_code=404, detail=f"WorkItem not found: {assignment.workitem_id}") from error
    except ValueError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error


def _sync_task_workitem_return(
    project_id: str,
    assignment: TaskAssignment,
    status: WorkItemStatus,
    payload: TaskReturnRequest,
) -> None:
    """Keep WorkItem lifecycle aligned with manual task-center returns."""
    try:
        engine.state_store.update_workitem(
            project_id,
            assignment.workitem_id,
            status,
            owner_agent=assignment.assigned_agent_id,
            result=payload.result_summary,
            output_artifact_ids=list(payload.output_artifact_ids),
            blocked_reason=payload.blocked_reason or None,
            failure_type="task_center" if status == WorkItemStatus.FAILED else "",
            failure_summary=(payload.blocked_reason or payload.result_summary) if status == WorkItemStatus.FAILED else "",
        )
    except KeyError as error:
        raise HTTPException(status_code=404, detail=f"WorkItem not found: {assignment.workitem_id}") from error
    except ValueError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error


def _task_workitem_payload(workitem) -> dict[str, object]:
    """Build the WorkItem subset needed by task center clients."""
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
    """Start one background step for a project."""
    _require_project_state(project_id)
    started = start_project_task(project_id, "step", "单步推进", engine.step_project)
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
    """Start a background run-to-end task for a project."""
    _require_project_state(project_id)
    started = start_project_task(project_id, "run", "运行到终态", engine.run_project)
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
    """Return partial live state for the standalone frontend."""
    return project_live_state(project_id)


@app.get("/api/settings/execution")
def execution_settings_api() -> JSONResponse:
    """Return execution scope settings."""
    config = load_execution_scope_config()
    return JSONResponse(asdict(config))


@app.post("/api/settings/execution")
async def save_execution_settings_api(request: Request) -> JSONResponse:
    """Persist execution scope settings from JSON."""
    payload = await request.json()
    config = ExecutionScopeConfig(
        requirement_design_enabled=bool(payload.get("requirement_design_enabled", True)),
        design_detail_enabled=bool(payload.get("design_detail_enabled", True)),
        design_collaboration_enabled=bool(payload.get("design_collaboration_enabled", True)),
        backend_development_enabled=bool(payload.get("backend_development_enabled", True)),
        frontend_development_enabled=bool(payload.get("frontend_development_enabled", True)),
        testing_enabled=bool(payload.get("testing_enabled", True)),
    )
    save_execution_scope_config(config)
    refresh_engine_execution_scope()
    return JSONResponse({"saved": True, "config": asdict(config)})


@app.get("/api/settings/cli")
def cli_settings_api() -> JSONResponse:
    """Return CLI binding settings and discovered tools."""
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
    """Persist CLI binding settings from JSON."""
    payload = await request.json()
    config = CLISelectionConfig(
        selected_cli_names=list(payload.get("selected_cli_names", [])),
        role_cli_bindings=dict(payload.get("role_cli_bindings", {})),
        codex_model=str(payload.get("codex_model", "gpt-5.4-mini")),
        codex_reasoning_effort=str(payload.get("codex_reasoning_effort", "medium")),
    )
    save_cli_selection_config(config)
    refresh_engine_cli_config()
    return JSONResponse({"saved": True, "config": asdict(config)})


@app.get("/api/settings/llm")
def llm_settings_api() -> JSONResponse:
    """Return LLM settings."""
    config = load_llm_runtime_config()
    return JSONResponse(asdict(config))


@app.post("/api/settings/llm")
async def save_llm_settings_api(request: Request) -> JSONResponse:
    """Persist LLM settings from JSON."""
    payload = await request.json()
    config = LLMRuntimeConfig(
        local=LLMHTTPConfig(
            base_url=str(payload.get("local", {}).get("base_url", "http://127.0.0.1:11434/v1")),
            model_name=str(payload.get("local", {}).get("model_name", "local-demo-model")),
            api_key=payload.get("local", {}).get("api_key"),
            timeout_seconds=float(payload.get("local", {}).get("timeout_seconds", 30.0)),
            enabled=bool(payload.get("local", {}).get("enabled", False)),
        ),
        cloud=LLMHTTPConfig(
            base_url=str(payload.get("cloud", {}).get("base_url", "https://api.openai.com/v1")),
            model_name=str(payload.get("cloud", {}).get("model_name", "gpt-demo-model")),
            api_key=payload.get("cloud", {}).get("api_key"),
            timeout_seconds=float(payload.get("cloud", {}).get("timeout_seconds", 30.0)),
            enabled=bool(payload.get("cloud", {}).get("enabled", False)),
        ),
        usage=LLMUsagePolicy(
            runner_enabled=bool(payload.get("usage", {}).get("runner_enabled", False)),
            runner_allowed_roles=list(payload.get("usage", {}).get("runner_allowed_roles", ["designer", "backend_engineer", "frontend_engineer", "tester"])),
            runner_allowed_kinds=list(payload.get("usage", {}).get("runner_allowed_kinds", [
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
            ])),
            preferred_backend=str(payload.get("usage", {}).get("preferred_backend", "cloud")),
        ),
    )
    save_llm_runtime_config(config)
    refresh_engine_llm_backend()
    return JSONResponse({"saved": True, "config": asdict(config)})


@app.get("/projects/{project_id}/runtime/stream")
def project_runtime_stream(project_id: str) -> StreamingResponse:
    """Stream live CLI output for the current project."""
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


@app.get("/api/todos")
def list_todos_api(
    request: Request,
    status: TodoListStatus = "all",
    query: str | None = None,
    q: str | None = None,
) -> JSONResponse:
    """Return all to-do items."""
    effective_query = q if q is not None else query
    todos = [item.to_dict() for item in get_todo_service(request).list_todos(status, effective_query)]
    return JSONResponse({"todos": todos})


@app.get("/api/todos/stats")
def todo_stats_api(request: Request) -> JSONResponse:
    """Return lightweight list statistics for the current session."""
    return JSONResponse(get_todo_service(request).get_stats())


@app.post("/api/todos", status_code=201)
def create_todo_api(request: Request, payload: TodoCreateRequest) -> JSONResponse:
    """Create one to-do item."""
    try:
        todo = get_todo_service(request).create_todo(payload.title, payload.completed, payload.content)
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error
    return JSONResponse({"todo": todo.to_dict()}, status_code=201)


@app.get("/api/todos/{todo_id}")
def get_todo_api(request: Request, todo_id: str) -> JSONResponse:
    """Fetch a to-do item by id."""
    todo = get_todo_service(request).get_todo(todo_id)
    if todo is None:
        raise HTTPException(status_code=404, detail="todo not found")
    return JSONResponse({"todo": todo.to_dict()})


@app.patch("/api/todos/{todo_id}")
def update_todo_api(request: Request, todo_id: str, payload: TodoUpdateRequest) -> JSONResponse:
    """Update a to-do item."""
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
    """Delete a to-do item."""
    deleted = get_todo_service(request).delete_todo(todo_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="todo not found")
    return JSONResponse({"deleted": True})


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
