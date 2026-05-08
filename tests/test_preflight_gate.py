"""Preflight gate persistence helper tests."""

import json

from conductor.preflight_gate import preflight_gate_path, read_preflight_gate, write_preflight_gate_payload


def test_preflight_gate_write_stamps_path_and_read_normalizes_payload(tmp_path) -> None:
    payload = {
        "ok": False,
        "preflight_gate": {
            "errors": ["local LLM preflight failed"],
        },
    }

    path = write_preflight_gate_payload(tmp_path, payload)
    persisted = json.loads(path.read_text(encoding="utf-8"))
    snapshot = read_preflight_gate(tmp_path)

    assert path == preflight_gate_path(tmp_path)
    assert persisted["preflight_gate"]["diagnostics_path"] == str(path)
    assert snapshot.recorded is True
    assert snapshot.path == str(path)
    assert snapshot.ok is False
    assert snapshot.status == "fail"
    assert snapshot.status_label == "失败"
    assert snapshot.errors == ["local LLM preflight failed"]


def test_preflight_gate_read_returns_not_recorded_for_missing_file(tmp_path) -> None:
    snapshot = read_preflight_gate(tmp_path)

    assert snapshot.recorded is False
    assert snapshot.path == ""
    assert snapshot.ok is None
    assert snapshot.status == "not_recorded"


def test_preflight_gate_read_handles_invalid_json(tmp_path) -> None:
    path = preflight_gate_path(tmp_path)
    path.parent.mkdir(parents=True)
    path.write_text("{not-json", encoding="utf-8")

    snapshot = read_preflight_gate(tmp_path)

    assert snapshot.recorded is True
    assert snapshot.path == str(path)
    assert snapshot.ok is False
    assert snapshot.status == "unreadable"
    assert snapshot.errors
