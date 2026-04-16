"""日志持久化测试。"""

from conductor.logging.store import ProjectLogStore


def test_project_log_store_can_append_and_read_entries(tmp_path) -> None:
    store = ProjectLogStore(tmp_path)

    store.append_event("project-1", 0, "项目已创建")
    store.append_event("project-1", 1, "进入阶段 design")
    entries = store.read_events("project-1")

    assert len(entries) == 2
    assert entries[0].project_id == "project-1"
    assert entries[1].message == "进入阶段 design"
