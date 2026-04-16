from pathlib import Path


def test_primary_button_label() -> None:
    content = Path("index.html").read_text(encoding="utf-8")
    assert "Create Task" in content
