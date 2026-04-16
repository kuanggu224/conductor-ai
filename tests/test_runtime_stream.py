"""Runtime stream store tests."""

from conductor.execution.runtime_stream import RuntimeStreamStore


def test_runtime_stream_store_tracks_lines_and_finish() -> None:
    store = RuntimeStreamStore(max_lines=3)

    store.start(
        project_id="project-1",
        workitem_id="workitem-1",
        agent_role="tester",
        backend="cli",
        cli_name="pytest",
    )
    store.append("project-1", "stdout", "line-1\n")
    store.append("project-1", "stderr", "line-2\n")
    store.finish("project-1", success=True, message="[done]")

    snapshot = store.snapshot("project-1")

    assert snapshot.running is False
    assert snapshot.status == "completed"
    assert snapshot.workitem_id == "workitem-1"
    assert snapshot.lines[-3:] == ["line-1", "stderr | line-2", "[done]"]
