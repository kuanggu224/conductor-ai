"""In-memory to-do application service."""

from __future__ import annotations

from fastapi import Request
from threading import Lock
from typing import Literal, cast
from uuid import uuid4

from conductor.todo.models import TodoItem

TODO_SESSION_COOKIE = "todo_session_id"
_REQUEST_STATE_SESSION_KEY = "todo_session_id"
_REQUEST_STATE_SERVICE_KEY = "todo_service"
TodoListStatus = Literal["all", "active", "completed"]


def ensure_todo_registry(app_state: object) -> dict[str, TodoService]:
    """Return the per-app todo registry, creating it when missing."""
    registry = getattr(app_state, "todo_services", None)
    if registry is None:
        registry = {}
        setattr(app_state, "todo_services", registry)
    return registry


def reset_todo_registry(app_state: object) -> dict[str, TodoService]:
    """Replace the per-app todo registry with a fresh empty mapping."""
    registry: dict[str, TodoService] = {}
    setattr(app_state, "todo_services", registry)
    return registry


def resolve_todo_service(request: Request) -> tuple[str, TodoService]:
    """Resolve the todo service bound to the request session."""
    cached_session_id = getattr(request.state, _REQUEST_STATE_SESSION_KEY, None)
    cached_service = getattr(request.state, _REQUEST_STATE_SERVICE_KEY, None)
    if cached_session_id is not None and cached_service is not None:
        registry = ensure_todo_registry(request.app.state)
        registry[cached_session_id] = cached_service
        return cached_session_id, cached_service

    registry = ensure_todo_registry(request.app.state)
    session_id = request.cookies.get(TODO_SESSION_COOKIE)
    if session_id is None or session_id not in registry:
        session_id = uuid4().hex
        registry[session_id] = create_todo_service()

    service = registry[session_id]
    setattr(request.state, _REQUEST_STATE_SESSION_KEY, session_id)
    setattr(request.state, _REQUEST_STATE_SERVICE_KEY, service)
    return session_id, service


class TodoService:
    """Store and manage to-do items in memory."""

    def __init__(self) -> None:
        self._items: dict[str, TodoItem] = {}
        self._lock = Lock()

    def list_todos(self, status: TodoListStatus = "all", query: str | None = None) -> list[TodoItem]:
        status = self._normalize_status(status)
        with self._lock:
            items = list(self._items.values())
            if status == "active":
                items = [item for item in items if not item.completed]
            elif status == "completed":
                items = [item for item in items if item.completed]
            cleaned_query = self._normalize_query(query)
            if cleaned_query is not None:
                items = [
                    item
                    for item in items
                    if cleaned_query in item.title.casefold() or cleaned_query in item.content.casefold()
                ]
            items.sort(key=lambda item: (-item.created_at.timestamp(), item.id))
            return [self._copy_item(item) for item in items]

    def count_todos(self) -> int:
        with self._lock:
            return len(self._items)

    def count_completed_todos(self) -> int:
        with self._lock:
            return sum(1 for item in self._items.values() if item.completed)

    def count_active_todos(self) -> int:
        with self._lock:
            return sum(1 for item in self._items.values() if not item.completed)

    def get_stats(self) -> dict[str, int]:
        """Return lightweight counters for the current todo collection."""
        with self._lock:
            total = len(self._items)
            completed = sum(1 for item in self._items.values() if item.completed)
        return {
            "total": total,
            "completed": completed,
            "active": total - completed,
        }

    def create_todo(self, title: str, completed: bool = False, content: str = "") -> TodoItem:
        cleaned_title = self._normalize_title(title)
        item = TodoItem(
            id=f"todo-{uuid4().hex[:12]}",
            title=cleaned_title,
            content=self._normalize_content(content),
            completed=self._normalize_completed(completed),
        )
        with self._lock:
            self._items[item.id] = item
        return self._copy_item(item)

    def get_todo(self, todo_id: str) -> TodoItem | None:
        with self._lock:
            item = self._items.get(todo_id)
            return self._copy_item(item) if item is not None else None

    def update_todo(
        self,
        todo_id: str,
        title: str | None = None,
        completed: bool | None = None,
        content: str | None = None,
    ) -> TodoItem | None:
        with self._lock:
            item = self._items.get(todo_id)
            if item is None:
                return None
            new_title = item.title
            new_completed = item.completed
            new_content = item.content
            if title is not None:
                new_title = self._normalize_title(title)
            if completed is not None:
                new_completed = self._normalize_completed(completed)
            if content is not None:
                new_content = self._normalize_content(content)
            item.title = new_title
            item.completed = new_completed
            item.content = new_content
            return self._copy_item(item)

    def delete_todo(self, todo_id: str) -> bool:
        with self._lock:
            return self._items.pop(todo_id, None) is not None

    def clear(self) -> None:
        with self._lock:
            self._items.clear()

    def reset(self) -> None:
        """Alias for clearing the in-memory store."""
        self.clear()

    @staticmethod
    def _normalize_title(title: object) -> str:
        """Validate and trim a user-supplied todo title."""
        if not isinstance(title, str):
            raise ValueError("title is required")
        cleaned_title = title.strip()
        if not cleaned_title:
            raise ValueError("title is required")
        if len(cleaned_title) > 200:
            raise ValueError("title is too long")
        return cleaned_title

    @staticmethod
    def _normalize_completed(completed: object) -> bool:
        """Validate a user-supplied completion flag."""
        if not isinstance(completed, bool):
            raise ValueError("completed must be a boolean")
        return completed

    @staticmethod
    def _normalize_content(content: object) -> str:
        """Validate and trim a user-supplied todo content string."""
        if not isinstance(content, str):
            raise ValueError("content must be a string")
        return content.strip()

    @staticmethod
    def _normalize_status(status: object) -> TodoListStatus:
        """Validate a user-supplied list filter."""
        if status not in {"all", "active", "completed"}:
            raise ValueError("status must be one of: all, active, completed")
        return cast(TodoListStatus, status)

    @staticmethod
    def _normalize_query(query: str | None) -> str | None:
        """Validate and normalize a search query."""
        if query is None:
            return None
        cleaned_query = query.strip().casefold()
        return cleaned_query or None

    @staticmethod
    def _copy_item(item: TodoItem) -> TodoItem:
        """Return a detached copy of a stored to-do item."""
        return TodoItem(
            id=item.id,
            title=item.title,
            content=item.content,
            completed=item.completed,
            created_at=item.created_at,
        )


def create_todo_service() -> TodoService:
    """Create a fresh in-memory todo service instance."""
    return TodoService()


__all__ = [
    "TODO_SESSION_COOKIE",
    "TodoListStatus",
    "create_todo_service",
    "TodoItem",
    "TodoService",
    "ensure_todo_registry",
    "reset_todo_registry",
    "resolve_todo_service",
]
