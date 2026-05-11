"""Harness 协议测试。"""

import sys

from conductor.harness.models import HarnessRequest
from conductor.harness.shell import ShellHarness
from conductor.harness.static_web import StaticWebHarness


def test_shell_harness_runs_python_command(tmp_path) -> None:
    harness = ShellHarness()
    request = HarnessRequest(
        command=[sys.executable, "-c", "print('harness-ok')"],
        working_directory=str(tmp_path),
        timeout_seconds=10,
        description="smoke",
    )

    result = harness.run(request)

    assert result.success is True
    assert result.exit_code == 0
    assert "harness-ok" in result.stdout
    assert result.stderr == ""
    assert result.duration_ms >= 0


def test_shell_harness_tracks_workspace_changes(tmp_path) -> None:
    harness = ShellHarness()
    request = HarnessRequest(
        command=[sys.executable, "-c", "from pathlib import Path; Path('changed.txt').write_text('ok', encoding='utf-8')"],
        working_directory=str(tmp_path),
        timeout_seconds=10,
        description="change-smoke",
        track_workspace_changes=True,
        workspace_root=str(tmp_path),
    )

    result = harness.run(request)

    assert result.success is True
    assert "changed.txt" in result.changed_files


def test_shell_harness_preserves_chinese_stdout_and_file_output(tmp_path) -> None:
    harness = ShellHarness()
    request = HarnessRequest(
        command=[
            sys.executable,
            "-c",
            (
                "import os, sys; "
                "from pathlib import Path; "
                "print(os.environ.get('PYTHONUTF8')); "
                "print('需求：费用报销审批'); "
                "Path('需求.txt').write_text('状态：已保存中文', encoding='utf-8')"
            ),
        ],
        working_directory=str(tmp_path),
        timeout_seconds=10,
        description="utf8-roundtrip",
        track_workspace_changes=True,
        workspace_root=str(tmp_path),
    )

    result = harness.run(request)

    assert result.success is True
    assert "1" in result.stdout.splitlines()
    assert "需求：费用报销审批" in result.stdout
    assert (tmp_path / "需求.txt").read_text(encoding="utf-8") == "状态：已保存中文"
    assert "需求.txt" in result.changed_files


def test_shell_harness_streams_output_lines(tmp_path) -> None:
    harness = ShellHarness()
    streamed: list[tuple[str, str]] = []
    request = HarnessRequest(
        command=[sys.executable, "-c", "print('line-1'); print('line-2')"],
        working_directory=str(tmp_path),
        timeout_seconds=10,
        description="stream-smoke",
        stream_callback=lambda channel, line: streamed.append((channel, line.strip())),
    )

    result = harness.run(request)

    assert result.success is True
    assert streamed == [("stdout", "line-1"), ("stdout", "line-2")]


def test_shell_harness_marks_timeout(tmp_path) -> None:
    harness = ShellHarness()
    request = HarnessRequest(
        command=[sys.executable, "-c", "import time; time.sleep(2)"],
        working_directory=str(tmp_path),
        timeout_seconds=0.1,
        description="timeout-smoke",
    )

    result = harness.run(request)

    assert result.success is False
    assert result.timed_out is True
    assert result.failure_reason == "timeout"


def test_static_web_harness_validates_basic_static_app(tmp_path) -> None:
    (tmp_path / "static").mkdir()
    (tmp_path / "index.html").write_text(
        """<!doctype html>
<html>
  <head>
    <title>Expense Tracker</title>
    <link rel="stylesheet" href="static/style.css">
  </head>
  <body>
    <form><input type="number" required><button type="submit">Add</button></form>
    <script src="static/app.js"></script>
  </body>
</html>
""",
        encoding="utf-8",
    )
    (tmp_path / "static" / "style.css").write_text("body { color: #111; }\n", encoding="utf-8")
    (tmp_path / "static" / "app.js").write_text(
        """
document.querySelector('form').addEventListener('submit', event => {
  event.preventDefault();
  const output = document.createElement('p');
  output.textContent = 'Added 12';
  document.body.appendChild(output);
});
""",
        encoding="utf-8",
    )

    result = StaticWebHarness().run(HarnessRequest(command=[], working_directory=str(tmp_path)))

    assert result.success is True
    assert result.exit_code == 0
    assert "Static Web Validation: PASS" in result.stdout
    assert "HTTP serving works" in result.stdout
    assert "JavaScript syntax valid: static/app.js" in result.stdout
    assert "Browser form interaction updated visible state" in result.stdout


def test_static_web_harness_uses_valid_rating_sample_value(tmp_path) -> None:
    (tmp_path / "static").mkdir()
    (tmp_path / "index.html").write_text(
        """<!doctype html>
<html>
  <head><title>Rating</title></head>
  <body>
    <form>
      <input id="title" required>
      <input id="rating" type="number" min="1" max="5" required>
      <button type="submit">Add</button>
    </form>
    <script src="static/app.js"></script>
  </body>
</html>
""",
        encoding="utf-8",
    )
    (tmp_path / "static" / "app.js").write_text(
        """
document.querySelector('form').addEventListener('submit', event => {
  event.preventDefault();
  const output = document.createElement('p');
  output.textContent = `Rating ${document.querySelector('#rating').value}`;
  document.body.appendChild(output);
});
""",
        encoding="utf-8",
    )

    result = StaticWebHarness().run(HarnessRequest(command=[], working_directory=str(tmp_path)))

    assert result.success is True
    assert "Browser form interaction updated visible state: 5" in result.stdout


def test_static_web_harness_reports_persistence_after_reload(tmp_path) -> None:
    (tmp_path / "static").mkdir()
    (tmp_path / "index.html").write_text(
        """<!doctype html>
<html>
  <head><title>Persist</title></head>
  <body>
    <form><input id="title" required><button type="submit">Add</button></form>
    <ul id="items"></ul>
    <script src="static/app.js"></script>
  </body>
</html>
""",
        encoding="utf-8",
    )
    (tmp_path / "static" / "app.js").write_text(
        """
const items = JSON.parse(localStorage.getItem('items') || '[]');
function render() {
  document.querySelector('#items').innerHTML = items.map(item => `<li>${item}</li>`).join('');
}
document.querySelector('form').addEventListener('submit', event => {
  event.preventDefault();
  items.push(document.querySelector('#title').value);
  localStorage.setItem('items', JSON.stringify(items));
  render();
});
render();
""",
        encoding="utf-8",
    )

    result = StaticWebHarness().run(HarnessRequest(command=[], working_directory=str(tmp_path)))

    assert result.success is True
    assert "Browser localStorage changed after form submit" in result.stdout
    assert "Browser reload preserved submitted values: sample" in result.stdout


def test_static_web_harness_exercises_input_button_ui_without_form(tmp_path) -> None:
    (tmp_path / "static").mkdir()
    (tmp_path / "index.html").write_text(
        """<!doctype html>
<html>
  <head><title>Reading List</title></head>
  <body>
    <input id="bookTitle" placeholder="Book title">
    <button id="addBook">Add</button>
    <button id="exportList">Export</button>
    <ul id="items"></ul>
    <script src="static/app.js"></script>
  </body>
</html>
""",
        encoding="utf-8",
    )
    (tmp_path / "static" / "app.js").write_text(
        """
const items = JSON.parse(localStorage.getItem('readingList') || '[]');
function render() {
  document.querySelector('#items').innerHTML = items.map(item => `<li>${item}</li>`).join('');
}
document.querySelector('#addBook').addEventListener('click', () => {
  const value = document.querySelector('#bookTitle').value;
  items.push(value);
  localStorage.setItem('readingList', JSON.stringify(items));
  render();
});
document.querySelector('#exportList').addEventListener('click', () => {
  const blob = new Blob([items.join('\\n')], { type: 'text/plain' });
  const a = document.createElement('a');
  a.href = URL.createObjectURL(blob);
  a.download = 'reading-list.txt';
  a.click();
});
render();
""",
        encoding="utf-8",
    )

    result = StaticWebHarness().run(HarnessRequest(command=[], working_directory=str(tmp_path)))

    assert result.success is True
    assert "Browser form interaction updated visible state: sample" in result.stdout
    assert "Browser localStorage changed after form submit" in result.stdout
    assert "Browser reload preserved submitted values: sample" in result.stdout
    assert "Browser export/download action triggered" in result.stdout


def test_static_web_harness_reports_filter_interaction(tmp_path) -> None:
    (tmp_path / "static").mkdir()
    (tmp_path / "index.html").write_text(
        """<!doctype html>
<html>
  <head><title>Reading List</title></head>
  <body>
    <form id="addForm">
      <input id="title" placeholder="Book title">
      <input id="author" placeholder="Author">
      <button type="submit">Add</button>
    </form>
    <input id="bookLookup" placeholder="\u641c\u7d22\u4e66\u7c4d">
    <input id="unrelatedNotes" placeholder="Notes">
    <ul id="items"></ul>
    <script src="static/app.js"></script>
  </body>
</html>
""",
        encoding="utf-8",
    )
    (tmp_path / "static" / "app.js").write_text(
        """
const items = JSON.parse(localStorage.getItem('items') || '[]');
function render(list = items) {
  document.querySelector('#items').innerHTML = list.map(item => `<li>${item.title} ${item.author}</li>`).join('');
}
document.querySelector('#addForm').addEventListener('submit', event => {
  event.preventDefault();
  items.push({
    title: document.querySelector('#title').value,
    author: document.querySelector('#author').value,
  });
  localStorage.setItem('items', JSON.stringify(items));
  render();
});
document.querySelector('#bookLookup').addEventListener('input', event => {
  const keyword = event.target.value.toLowerCase();
  render(items.filter(item => item.title.toLowerCase().includes(keyword) || item.author.toLowerCase().includes(keyword)));
});
render();
""",
        encoding="utf-8",
    )

    result = StaticWebHarness().run(HarnessRequest(command=[], working_directory=str(tmp_path)))

    assert result.success is True
    assert "Browser filter interaction changed visible results" in result.stdout


def test_static_web_harness_reports_select_filter_interaction(tmp_path) -> None:
    (tmp_path / "static").mkdir()
    (tmp_path / "index.html").write_text(
        """<!doctype html>
<html>
  <head><title>Status Filter</title></head>
  <body>
    <form id="addForm">
      <input id="title" placeholder="Book title">
      <select id="status">
        <option value="unread">Unread</option>
        <option value="done">Done</option>
      </select>
      <button type="submit">Add</button>
    </form>
    <select id="statusFilter" aria-label="Filter status">
      <option value="all">All</option>
      <option value="unread">Unread</option>
      <option value="done">Done</option>
    </select>
    <ul id="items"></ul>
    <script src="static/app.js"></script>
  </body>
</html>
""",
        encoding="utf-8",
    )
    (tmp_path / "static" / "app.js").write_text(
        """
const items = JSON.parse(localStorage.getItem('items') || '[]');
let filter = 'all';
function render() {
  const visible = filter === 'all' ? items : items.filter(item => item.status === filter);
  document.querySelector('#items').innerHTML = visible.map(item => `<li>${item.title} ${item.status}</li>`).join('');
}
document.querySelector('#addForm').addEventListener('submit', event => {
  event.preventDefault();
  items.push({
    title: document.querySelector('#title').value,
    status: document.querySelector('#status').value,
  });
  localStorage.setItem('items', JSON.stringify(items));
  render();
});
document.querySelector('#statusFilter').addEventListener('change', event => {
  filter = event.target.value;
  render();
});
render();
""",
        encoding="utf-8",
    )

    result = StaticWebHarness().run(HarnessRequest(command=[], working_directory=str(tmp_path)))

    assert result.success is True
    assert "Browser filter interaction changed visible results" in result.stdout


def test_static_web_harness_reports_delete_interaction(tmp_path) -> None:
    (tmp_path / "static").mkdir()
    (tmp_path / "index.html").write_text(
        """<!doctype html>
<html>
  <head><title>Delete Item</title></head>
  <body>
    <form id="addForm">
      <input id="title" placeholder="Book title">
      <button type="submit">Add</button>
    </form>
    <button id="exportList">Export</button>
    <ul id="items"></ul>
    <script src="static/app.js"></script>
  </body>
</html>
""",
        encoding="utf-8",
    )
    (tmp_path / "static" / "app.js").write_text(
        """
let items = JSON.parse(localStorage.getItem('items') || '[]');
function save() {
  localStorage.setItem('items', JSON.stringify(items));
}
function render() {
  document.querySelector('#items').innerHTML = items
    .map((item, index) => `<li>${item.title} <button class="delete" data-index="${index}">Delete</button></li>`)
    .join('');
}
document.querySelector('#addForm').addEventListener('submit', event => {
  event.preventDefault();
  items.push({ title: document.querySelector('#title').value });
  save();
  render();
});
document.querySelector('#items').addEventListener('click', event => {
  if (!event.target.matches('.delete')) return;
  items.splice(Number(event.target.dataset.index), 1);
  save();
  render();
});
document.querySelector('#exportList').addEventListener('click', () => {
  const blob = new Blob([items.map(item => item.title).join('\\n')], { type: 'text/plain' });
  const a = document.createElement('a');
  a.href = URL.createObjectURL(blob);
  a.download = 'items.txt';
  a.click();
});
render();
""",
        encoding="utf-8",
    )

    result = StaticWebHarness().run(HarnessRequest(command=[], working_directory=str(tmp_path)))

    assert result.success is True
    assert "Browser export/download action triggered" in result.stdout
    assert "Browser delete interaction removed visible item" in result.stdout


def test_static_web_harness_fails_missing_local_asset(tmp_path) -> None:
    (tmp_path / "index.html").write_text(
        """<!doctype html>
<html>
  <head><title>Broken</title></head>
  <body><script src="static/missing.js"></script></body>
</html>
""",
        encoding="utf-8",
    )

    result = StaticWebHarness().run(HarnessRequest(command=[], working_directory=str(tmp_path)))

    assert result.success is False
    assert result.exit_code == 1
    assert result.failure_reason == "static_web_validation_failed"
    assert "Missing script asset: static/missing.js" in result.stdout


def test_static_web_harness_fails_mojibake_text(tmp_path) -> None:
    (tmp_path / "index.html").write_text(
        """<!doctype html>
<html>
  <head><title>鏈湴璇讳功娓呭崟</title></head>
  <body><h1>鏈湴璇讳功娓呭崟</h1></body>
</html>
""",
        encoding="utf-8",
    )

    result = StaticWebHarness().run(HarnessRequest(command=[], working_directory=str(tmp_path)))

    assert result.success is False
    assert result.exit_code == 1
    assert "mojibake/corrupted UTF-8 text" in result.stdout


def test_static_web_harness_fails_mojibake_javascript_asset(tmp_path) -> None:
    (tmp_path / "static").mkdir()
    (tmp_path / "index.html").write_text(
        """<!doctype html>
<html>
  <head><title>Reading List</title></head>
  <body><script src="static/app.js"></script></body>
</html>
""",
        encoding="utf-8",
    )
    corrupted_label = "\u6748\u64b3\u53c6\u6d94\ufe40\u6095"
    (tmp_path / "static" / "app.js").write_text(f"const title = '{corrupted_label}';\n", encoding="utf-8")

    result = StaticWebHarness().run(HarnessRequest(command=[], working_directory=str(tmp_path)))

    assert result.success is False
    assert result.exit_code == 1
    assert "script asset appears to contain mojibake/corrupted UTF-8 text: static/app.js" in result.stdout


def test_static_web_harness_fails_when_form_submit_does_not_change_visible_state(tmp_path) -> None:
    (tmp_path / "static").mkdir()
    (tmp_path / "index.html").write_text(
        """<!doctype html>
<html>
  <head><title>Noop</title></head>
  <body>
    <form><input id="amount" type="number" required><button type="submit">Add</button></form>
    <script src="static/app.js"></script>
  </body>
</html>
""",
        encoding="utf-8",
    )
    (tmp_path / "static" / "app.js").write_text(
        "document.querySelector('form').addEventListener('submit', event => event.preventDefault());\n",
        encoding="utf-8",
    )

    result = StaticWebHarness().run(HarnessRequest(command=[], working_directory=str(tmp_path)))

    assert result.success is False
    assert "Browser form submit did not change visible page state" in result.stdout
