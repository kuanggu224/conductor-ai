from conductor.todo.service import TodoService


def test_todo_service_supports_crud_lifecycle() -> None:
    service = TodoService()

    created = service.create_todo("Write backend")
    assert created.title == "Write backend"
    assert created.completed is False
    assert created.content == ""

    listed = service.list_todos()
    assert listed == [created]

    fetched = service.get_todo(created.id)
    assert fetched == created

    updated = service.update_todo(created.id, completed=True)
    assert updated is not None
    assert updated.completed is True

    content_updated = service.update_todo(created.id, content="Ship it")
    assert content_updated is not None
    assert content_updated.content == "Ship it"

    deleted = service.delete_todo(created.id)
    assert deleted is True
    assert service.get_todo(created.id) is None


def test_todo_service_supports_status_filtering() -> None:
    service = TodoService()
    active = service.create_todo("Active task")
    completed = service.create_todo("Completed task", completed=True)

    assert service.list_todos() == [completed, active]
    assert service.list_todos("active") == [active]
    assert service.list_todos("completed") == [completed]


def test_todo_service_exposes_basic_statistics() -> None:
    service = TodoService()
    service.create_todo("Active task")
    service.create_todo("Completed task", completed=True)

    assert service.count_todos() == 2
    assert service.count_completed_todos() == 1
    assert service.count_active_todos() == 1
    assert service.get_stats() == {"total": 2, "completed": 1, "active": 1}


def test_todo_service_supports_keyword_querying() -> None:
    service = TodoService()
    backend = service.create_todo("Write backend", content="Implement todo query")
    frontend = service.create_todo("Write frontend", content="Render todo list")
    other = service.create_todo("Review notes", content="Discuss backlog")

    assert service.list_todos(query="todo") == [frontend, backend]
    assert service.list_todos(query=" backend ") == [backend]
    assert service.list_todos(query="missing") == []
    assert service.list_todos(query="") == [other, frontend, backend]


def test_todo_service_rejects_invalid_status_filters() -> None:
    service = TodoService()

    try:
        service.list_todos("invalid")  # type: ignore[arg-type]
    except ValueError as error:
        assert str(error) == "status must be one of: all, active, completed"
    else:
        raise AssertionError("expected ValueError")


def test_todo_service_rejects_blank_titles() -> None:
    service = TodoService()

    try:
        service.create_todo("   ")
    except ValueError as error:
        assert str(error) == "title is required"
    else:
        raise AssertionError("expected ValueError")


def test_todo_service_rejects_overlong_titles() -> None:
    service = TodoService()

    try:
        service.create_todo("x" * 201)
    except ValueError as error:
        assert str(error) == "title is too long"
    else:
        raise AssertionError("expected ValueError")


def test_todo_service_rejects_non_string_titles() -> None:
    service = TodoService()

    for invalid_title in [None, 123, [], {}]:
        try:
            service.create_todo(invalid_title)  # type: ignore[arg-type]
        except ValueError as error:
            assert str(error) == "title is required"
        else:
            raise AssertionError("expected ValueError")


def test_todo_service_rejects_non_boolean_completed_flags() -> None:
    service = TodoService()

    for invalid_completed in ["true", 1, None, []]:
        try:
            service.create_todo("Write backend", completed=invalid_completed)  # type: ignore[arg-type]
        except ValueError as error:
            assert str(error) == "completed must be a boolean"
        else:
            raise AssertionError("expected ValueError")


def test_todo_service_update_is_atomic_when_validation_fails() -> None:
    service = TodoService()
    created = service.create_todo("Write backend")

    try:
        service.update_todo(created.id, title="Updated title", completed="true")  # type: ignore[arg-type]
    except ValueError as error:
        assert str(error) == "completed must be a boolean"
    else:
        raise AssertionError("expected ValueError")

    assert service.get_todo(created.id) == created
