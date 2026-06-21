from fastapi.testclient import TestClient

from app.todo_app import app, get_todo_service


def test_todo_app_supports_crud_lifecycle() -> None:
    client = TestClient(app)

    create_response = client.post("/api/todos", json={"title": "Write backend", "content": "Finish API"})
    assert create_response.status_code == 201
    todo = create_response.json()["todo"]
    assert todo["title"] == "Write backend"
    assert todo["completed"] is False
    assert todo["content"] == "Finish API"

    todo_id = todo["id"]

    list_response = client.get("/api/todos")
    assert list_response.status_code == 200
    assert list_response.json()["todos"] == [todo]

    update_response = client.patch(f"/api/todos/{todo_id}", json={"completed": True, "content": "Ship it"})
    assert update_response.status_code == 200
    assert update_response.json()["todo"]["completed"] is True
    assert update_response.json()["todo"]["content"] == "Ship it"

    get_response = client.get(f"/api/todos/{todo_id}")
    assert get_response.status_code == 200
    assert get_response.json()["todo"]["completed"] is True

    delete_response = client.delete(f"/api/todos/{todo_id}")
    assert delete_response.status_code == 200
    assert delete_response.json() == {"deleted": True}

    missing_response = client.get(f"/api/todos/{todo_id}")
    assert missing_response.status_code == 404


def test_todo_app_supports_status_filtering() -> None:
    client = TestClient(app)

    active_response = client.post("/api/todos", json={"title": "Active task"})
    completed_response = client.post("/api/todos", json={"title": "Completed task", "completed": True})

    active_todo = active_response.json()["todo"]
    completed_todo = completed_response.json()["todo"]

    active_list_response = client.get("/api/todos", params={"status": "active"})
    assert active_list_response.status_code == 200
    assert active_list_response.json()["todos"] == [active_todo]

    completed_list_response = client.get("/api/todos", params={"status": "completed"})
    assert completed_list_response.status_code == 200
    assert completed_list_response.json()["todos"] == [completed_todo]

    all_list_response = client.get("/api/todos")
    assert all_list_response.status_code == 200
    assert all_list_response.json()["todos"] == [completed_todo, active_todo]


def test_todo_app_supports_keyword_querying() -> None:
    client = TestClient(app)

    query_response = client.post("/api/todos", json={"title": "Write backend", "content": "Implement todo query"})
    render_response = client.post("/api/todos", json={"title": "Write backend", "content": "Render todo list"})
    client.post("/api/todos", json={"title": "Review notes", "content": "Discuss backlog"})

    query_backend_todo = query_response.json()["todo"]
    render_backend_todo = render_response.json()["todo"]

    query_response = client.get("/api/todos", params={"q": "todo"})
    assert query_response.status_code == 200
    assert query_response.json()["todos"] == [render_backend_todo, query_backend_todo]

    title_query_response = client.get("/api/todos", params={"q": " backend "})
    assert title_query_response.status_code == 200
    assert title_query_response.json()["todos"] == [render_backend_todo, query_backend_todo]


def test_todo_app_exposes_basic_stats() -> None:
    client = TestClient(app)

    client.post("/api/todos", json={"title": "First task"})
    client.post("/api/todos", json={"title": "Second task", "completed": True})

    response = client.get("/api/todos/stats")

    assert response.status_code == 200
    assert response.json() == {"total": 2, "completed": 1, "active": 1}


def test_todo_app_allows_null_title_on_partial_update() -> None:
    client = TestClient(app)

    create_response = client.post("/api/todos", json={"title": "Write backend"})
    todo_id = create_response.json()["todo"]["id"]

    response = client.patch(f"/api/todos/{todo_id}", json={"title": None, "completed": True})

    assert response.status_code == 200
    assert response.json()["todo"]["completed"] is True
    assert response.json()["todo"]["title"] == "Write backend"


def test_todo_app_rejects_blank_and_whitespace_titles() -> None:
    client = TestClient(app)

    assert client.post("/api/todos", json={"title": ""}).status_code == 422
    assert client.post("/api/todos", json={"title": "   "}).status_code == 422


def test_todo_app_rejects_non_string_titles_and_non_boolean_flags() -> None:
    client = TestClient(app)

    assert client.post("/api/todos", json={"title": 123}).status_code == 422
    assert client.post("/api/todos", json={"title": "Write backend", "completed": "true"}).status_code == 422


def test_todo_app_exposes_basic_root_and_health_checks() -> None:
    client = TestClient(app)

    assert client.get("/").json() == {"message": "Simple to-do app", "status": "ready"}
    assert client.get("/health").json() == {"ok": True}


def test_todo_app_uses_a_fresh_store_for_each_client_session() -> None:
    with TestClient(app) as first_client:
        create_response = first_client.post("/api/todos", json={"title": "Session one"})
        assert create_response.status_code == 201
        assert first_client.get("/api/todos").json()["todos"]

    with TestClient(app) as second_client:
        list_response = second_client.get("/api/todos")
        assert list_response.status_code == 200
        assert list_response.json() == {"todos": []}


def test_todo_app_starts_with_a_fresh_store_for_each_lifecycle() -> None:
    with TestClient(app) as client:
        create_response = client.post("/api/todos", json={"title": "First run"})
        assert create_response.status_code == 201

    with TestClient(app) as client:
        list_response = client.get("/api/todos")
        assert list_response.status_code == 200
        assert list_response.json() == {"todos": []}


def test_todo_app_resets_in_memory_store_per_lifecycle() -> None:
    with TestClient(app) as client:
        create_response = client.post("/api/todos", json={"title": "Temporary task"})
        assert create_response.status_code == 201
        assert client.get("/api/todos").json()["todos"]

    with TestClient(app) as client:
        list_response = client.get("/api/todos")
        assert list_response.status_code == 200
        assert list_response.json() == {"todos": []}


def test_todo_app_isolates_todos_per_client_session() -> None:
    with TestClient(app) as first_client, TestClient(app) as second_client:
        first_response = first_client.post("/api/todos", json={"title": "First client"})
        assert first_response.status_code == 201
        first_todo = first_response.json()["todo"]

        second_response = second_client.get("/api/todos")
        assert second_response.status_code == 200
        assert second_response.json() == {"todos": []}

        first_list_response = first_client.get("/api/todos")
        assert first_list_response.status_code == 200
        assert first_list_response.json() == {"todos": [first_todo]}


def test_todo_app_uses_a_separate_default_store_outside_requests() -> None:
    with TestClient(app) as client:
        response = client.post("/api/todos", json={"title": "Session todo"})
        assert response.status_code == 201

    default_service = get_todo_service()

    assert default_service.list_todos() == []
