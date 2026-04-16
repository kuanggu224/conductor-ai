"""Board 后台执行测试。"""

from threading import Event

from fastapi.testclient import TestClient

from app import board


def test_step_route_starts_background_task_without_waiting(monkeypatch) -> None:
    """step 路由应立即返回，由后台线程继续推进。"""
    started = Event()
    release = Event()

    def slow_step(project_id: str) -> None:
        started.set()
        release.wait(timeout=2)

    board.task_statuses.clear()
    monkeypatch.setattr(board.engine, "step_project", slow_step)
    client = TestClient(board.app)

    try:
        response = client.get("/projects/project-async-test/step", follow_redirects=False)
        started.wait(timeout=1)

        status = board.get_project_task_status("project-async-test")

        assert response.status_code == 303
        assert status.running is True
        assert status.action == "step"
    finally:
        release.set()
        board.task_statuses.clear()


def test_project_detail_page_renders_runtime_and_meeting_sections() -> None:
    """项目详情页应返回 200，并包含新版主舞台区域。"""
    client = TestClient(board.app)
    state = board.engine.create_project(
        requirement="实现一个包含 API、前端交互和测试的最小系统",
        project_root=r"C:\99_self\conductor\conductor-ai",
    )

    response = client.get(f"/projects/{state.project.id}")

    assert response.status_code == 200
    assert "需求设计会议桌" in response.text
    assert "Multi-Agent Workflow" in response.text
    assert "当前执行上下文" in response.text
    assert "runtime-stream-log" in response.text
    assert "http-equiv=\"refresh\"" not in response.text


def test_project_live_endpoint_returns_runtime_payload() -> None:
    """项目 live 接口应返回局部刷新的结构化数据。"""
    client = TestClient(board.app)
    state = board.engine.create_project(
        requirement="实现一个包含 API、前端交互和测试的最小系统",
        project_root=r"C:\99_self\conductor\conductor-ai",
    )

    response = client.get(f"/projects/{state.project.id}/live")

    assert response.status_code == 200
    payload = response.json()
    assert payload["project_id"] == state.project.id
    assert "project_status_label" in payload
    assert "current_stage_label" in payload
    assert "task_status" in payload
    assert "execution_runtime" in payload
