from fastapi.testclient import TestClient

from app import board


def test_todo_api_supports_crud_lifecycle() -> None:
    client = TestClient(board.app)

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


def test_todo_api_supports_status_filtering() -> None:
    client = TestClient(board.app)

    active_response = client.post("/api/todos", json={"title": "Active task"})
    completed_response = client.post("/api/todos", json={"title": "Completed task", "completed": True})

    active_todo = active_response.json()["todo"]
    completed_todo = completed_response.json()["todo"]

    all_response = client.get("/api/todos")
    assert all_response.status_code == 200
    assert all_response.json()["todos"] == [completed_todo, active_todo]

    active_list_response = client.get("/api/todos", params={"status": "active"})
    assert active_list_response.status_code == 200
    assert active_list_response.json()["todos"] == [active_todo]

    completed_list_response = client.get("/api/todos", params={"status": "completed"})
    assert completed_list_response.status_code == 200
    assert completed_list_response.json()["todos"] == [completed_todo]


def test_todo_api_supports_keyword_querying() -> None:
    client = TestClient(board.app)

    backend_response = client.post("/api/todos", json={"title": "Write backend", "content": "Implement todo query"})
    frontend_response = client.post("/api/todos", json={"title": "Write frontend", "content": "Render todo list"})
    client.post("/api/todos", json={"title": "Review notes", "content": "Discuss backlog"})

    backend_todo = backend_response.json()["todo"]
    frontend_todo = frontend_response.json()["todo"]

    query_response = client.get("/api/todos", params={"q": "todo"})
    assert query_response.status_code == 200
    assert query_response.json()["todos"] == [frontend_todo, backend_todo]

    title_query_response = client.get("/api/todos", params={"q": " backend "})
    assert title_query_response.status_code == 200
    assert title_query_response.json()["todos"] == [backend_todo]


def test_todo_api_exposes_basic_stats() -> None:
    client = TestClient(board.app)

    client.post("/api/todos", json={"title": "First task"})
    client.post("/api/todos", json={"title": "Second task", "completed": True})

    response = client.get("/api/todos/stats")

    assert response.status_code == 200
    assert response.json() == {"total": 2, "completed": 1, "active": 1}


def test_todo_api_rejects_blank_titles() -> None:
    client = TestClient(board.app)

    response = client.post("/api/todos", json={"title": ""})

    assert response.status_code == 422

    whitespace_response = client.post("/api/todos", json={"title": "   "})

    assert whitespace_response.status_code == 422


def test_todo_api_rejects_non_string_titles_and_non_boolean_flags() -> None:
    client = TestClient(board.app)

    assert client.post("/api/todos", json={"title": 123}).status_code == 422
    assert client.post("/api/todos", json={"title": "Write backend", "completed": "true"}).status_code == 422
