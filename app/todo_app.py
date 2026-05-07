"""Standalone simple to-do application."""

from __future__ import annotations

import argparse
from contextlib import asynccontextmanager
from collections.abc import AsyncIterator, Sequence
import os
import sys
from typing import Annotated, Awaitable, Callable

from fastapi import APIRouter, FastAPI, HTTPException, Request, Response
from pydantic import BaseModel, StrictBool, StringConstraints
import uvicorn

from conductor.io.encoding import configure_utf8_stdio
from conductor.todo.models import TodoItem
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


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Create a fresh in-memory todo store for each app lifecycle."""
    _initialize_todo_state(app.state)
    yield


router = APIRouter()

__all__ = [
    "app",
    "build_app",
    "OptionalTodoTitle",
    "TodoItem",
    "create_todo_service",
    "create_app",
    "get_todo_service",
    "lifespan",
    "main",
    "router",
    "TODO_SESSION_COOKIE",
    "TodoTitle",
    "TodoContent",
    "TodoCreateRequest",
    "TodoUpdateRequest",
    "TodoService",
    "TodoListStatus",
    "ensure_todo_registry",
    "reset_todo_registry",
    "resolve_todo_service",
]


TodoTitle = Annotated[str, StringConstraints(strict=True, strip_whitespace=True, min_length=1, max_length=200)]
TodoContent = Annotated[str, StringConstraints(strict=True, strip_whitespace=True, max_length=1000)]
OptionalTodoTitle = TodoTitle | None
OptionalTodoContent = TodoContent | None


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


def get_todo_service(request: Request | None = None) -> TodoService:
    """Return the active todo service for the current app lifecycle or request."""
    if request is None:
        # Background callers and tests use the app-level default store.
        return _ensure_todo_service()
    cached_service = getattr(request.state, "todo_service", None)
    if cached_service is not None:
        return cached_service
    _, service = resolve_todo_service(request)
    return service


def _ensure_todo_service(app_state: object | None = None) -> TodoService:
    """Create and attach the in-memory store when it is missing."""
    target_state = app.state if app_state is None else app_state
    registry = ensure_todo_registry(target_state)
    service = registry.get("default")
    if service is None:
        service = create_todo_service()
        registry["default"] = service
    return service


def _initialize_todo_state(app_state: object) -> TodoService:
    """Reset the shared registry and create the default app-level store."""
    reset_todo_registry(app_state)
    return _ensure_todo_service(app_state)


async def todo_session_middleware(
    request: Request,
    call_next: Callable[[Request], Awaitable[Response]],
) -> Response:
    """Bind each client session to its own in-memory todo store."""
    # Keep the session affinity in middleware so the API handlers stay thin.
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


def create_app() -> FastAPI:
    """Return the configured FastAPI application."""
    global app
    todo_app = FastAPI(title="Simple Todo App", lifespan=lifespan)
    _initialize_todo_state(todo_app.state)
    todo_app.include_router(router)
    todo_app.middleware("http")(todo_session_middleware)
    app = todo_app
    return todo_app


def build_app() -> FastAPI:
    """Compatibility alias for callers that expect a build-style factory."""
    return create_app()


@router.get("/")
def root() -> dict[str, str]:
    """Return a tiny application summary."""
    return {"message": "Simple to-do app", "status": "ready"}


@router.get("/health")
def health() -> dict[str, bool]:
    """Expose a simple readiness check."""
    return {"ok": True}


@router.get("/api/todos")
def list_todos_api(
    request: Request,
    status: TodoListStatus = "all",
    query: str | None = None,
    q: str | None = None,
) -> dict[str, list[dict[str, object]]]:
    """Return all to-do items."""
    effective_query = q if q is not None else query
    todos = [item.to_dict() for item in get_todo_service(request).list_todos(status, effective_query)]
    return {"todos": todos}


@router.get("/api/todos/stats")
def todo_stats_api(request: Request) -> dict[str, int]:
    """Return lightweight list statistics for the current session."""
    service = get_todo_service(request)
    return service.get_stats()


@router.post("/api/todos", status_code=201)
def create_todo_api(request: Request, payload: TodoCreateRequest) -> dict[str, dict[str, object]]:
    """Create one to-do item."""
    try:
        todo = get_todo_service(request).create_todo(payload.title, payload.completed, payload.content)
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error
    return {"todo": todo.to_dict()}


@router.get("/api/todos/{todo_id}")
def get_todo_api(request: Request, todo_id: str) -> dict[str, dict[str, object]]:
    """Fetch a to-do item by id."""
    todo = get_todo_service(request).get_todo(todo_id)
    if todo is None:
        raise HTTPException(status_code=404, detail="todo not found")
    return {"todo": todo.to_dict()}


@router.patch("/api/todos/{todo_id}")
def update_todo_api(request: Request, todo_id: str, payload: TodoUpdateRequest) -> dict[str, dict[str, object]]:
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
    return {"todo": todo.to_dict()}


@router.delete("/api/todos/{todo_id}")
def delete_todo_api(request: Request, todo_id: str) -> dict[str, bool]:
    """Delete a to-do item."""
    deleted = get_todo_service(request).delete_todo(todo_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="todo not found")
    return {"deleted": True}


def _build_parser() -> argparse.ArgumentParser:
    """Build the small CLI used by ``python -m app.todo_app``."""
    parser = argparse.ArgumentParser(prog="python -m app.todo_app")
    parser.add_argument("--host", default=os.getenv("TODO_APP_HOST", "127.0.0.1"))
    parser.add_argument("--port", type=int, default=int(os.getenv("TODO_APP_PORT", "8000")))
    parser.add_argument("--reload", action="store_true")
    return parser


def main(argv: Sequence[str] | None = None) -> int | None:
    """Run the standalone app with Uvicorn."""
    args_list = list(argv) if argv is not None else list(sys.argv[1:])
    if args_list is not None and args_list and not args_list[0].startswith("-"):
        # Preserve legacy script-style invocation that passed a free-form requirement,
        # but keep any real CLI flags that follow it.
        first_flag_index = next((index for index, token in enumerate(args_list) if token.startswith("-")), len(args_list))
        args_list = args_list[first_flag_index:] if first_flag_index < len(args_list) else []
    args = _build_parser().parse_args(args_list)
    # Use the factory so each process gets a fresh FastAPI instance.
    # This keeps TestClient and CLI startup isolated from each other.
    return uvicorn.run(
        "app.todo_app:create_app",
        host=args.host,
        port=args.port,
        reload=args.reload,
        factory=True,
    )


# Keep a module-level app for importers, while create_app() remains the
# factory used by Uvicorn reload/factory mode.
app: FastAPI = create_app()


if __name__ == "__main__":
    main()
