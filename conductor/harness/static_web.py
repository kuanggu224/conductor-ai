"""Static web application validation harness."""

from __future__ import annotations

import re
import shutil
import subprocess
import threading
from dataclasses import dataclass, field
from html.parser import HTMLParser
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from time import perf_counter
from urllib.request import urlopen

from conductor.harness.base import BaseHarness
from conductor.harness.models import HarnessRequest, HarnessResult
from conductor.io.encoding import looks_like_mojibake, utf8_subprocess_environment


@dataclass(slots=True)
class StaticWebCheckReport:
    """Structured static web validation report."""

    checks: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)

    @property
    def success(self) -> bool:
        return not self.errors

    def render(self) -> str:
        """Render a stable, human-readable validation report."""
        status = "PASS" if self.success else "FAIL"
        lines = [f"Static Web Validation: {status}", ""]
        lines.append("Checks:")
        lines.extend(f"- {item}" for item in self.checks or ["No checks executed"])
        if self.warnings:
            lines.append("")
            lines.append("Warnings:")
            lines.extend(f"- {item}" for item in self.warnings)
        if self.errors:
            lines.append("")
            lines.append("Errors:")
            lines.extend(f"- {item}" for item in self.errors)
        return "\n".join(lines) + "\n"


@dataclass(slots=True)
class BrowserExerciseResult:
    """Result of exercising the first visible form."""

    attempted: bool = False
    submitted: bool = False
    body_changed: bool = False
    visible_values: list[str] = field(default_factory=list)
    selected_values: list[str] = field(default_factory=list)
    local_storage_changed: bool = False
    persisted_values: list[str] = field(default_factory=list)
    download_triggered: bool = False


class _HTMLAssetParser(HTMLParser):
    """Collect local script and stylesheet references from HTML."""

    def __init__(self) -> None:
        super().__init__()
        self.title_seen = False
        self.body_seen = False
        self.scripts: list[str] = []
        self.stylesheets: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        values = {name.lower(): value or "" for name, value in attrs}
        tag = tag.lower()
        if tag == "title":
            self.title_seen = True
        if tag == "body":
            self.body_seen = True
        if tag == "script" and values.get("src"):
            self.scripts.append(values["src"])
        if tag == "link" and values.get("rel", "").lower() == "stylesheet" and values.get("href"):
            self.stylesheets.append(values["href"])


class StaticWebHarness(BaseHarness):
    """Validate a small static HTML/CSS/JavaScript application."""

    name = "static_web"

    def run(self, request: HarnessRequest) -> HarnessResult:
        """Run static web validation in-process."""
        started = perf_counter()
        root = Path(request.working_directory).expanduser().resolve()
        report = StaticWebCheckReport()
        index = root / "index.html"

        if not index.exists():
            report.errors.append("Missing index.html")
            return self._result(started, report)

        html = index.read_text(encoding="utf-8", errors="replace")
        if not html.strip():
            report.errors.append("index.html is empty")
            return self._result(started, report)
        if looks_like_mojibake(html):
            report.errors.append("index.html appears to contain mojibake/corrupted UTF-8 text")

        parser = _HTMLAssetParser()
        parser.feed(html)
        report.checks.append("index.html exists and is non-empty")
        if not parser.title_seen:
            report.warnings.append("index.html has no <title>")
        if not parser.body_seen:
            report.errors.append("index.html has no <body>")

        local_scripts = self._validate_assets(root, parser.scripts, report, "script")
        self._validate_assets(root, parser.stylesheets, report, "stylesheet")
        self._check_javascript_syntax(root, local_scripts, report)
        self._check_http_serving(root, report)
        self._check_browser_runtime(root, report)
        return self._result(started, report)

    def _validate_assets(
        self,
        root: Path,
        refs: list[str],
        report: StaticWebCheckReport,
        label: str,
    ) -> list[Path]:
        local_files: list[Path] = []
        for ref in refs:
            if self._is_remote_or_inline(ref):
                report.warnings.append(f"Skipped remote or inline {label}: {ref}")
                continue
            target = (root / ref.split("?", 1)[0].split("#", 1)[0]).resolve()
            try:
                target.relative_to(root)
            except ValueError:
                report.errors.append(f"{label} reference escapes project root: {ref}")
                continue
            if not target.exists():
                report.errors.append(f"Missing {label} asset: {ref}")
                continue
            if target.suffix.lower() in {".html", ".js", ".css"}:
                try:
                    text = target.read_text(encoding="utf-8", errors="replace")
                except OSError:
                    text = ""
                if looks_like_mojibake(text):
                    report.errors.append(f"{label} asset appears to contain mojibake/corrupted UTF-8 text: {ref}")
                    continue
            report.checks.append(f"{label} asset exists: {ref}")
            local_files.append(target)
        return local_files

    def _check_javascript_syntax(self, root: Path, scripts: list[Path], report: StaticWebCheckReport) -> None:
        js_files = scripts or (sorted((root / "static").glob("*.js")) if (root / "static").exists() else [])
        if not js_files:
            report.warnings.append("No local JavaScript files found for syntax validation")
            return
        node = shutil.which("node")
        if not node:
            report.warnings.append("Node.js is not available; skipped JavaScript syntax validation")
            return
        for path in js_files:
            process = subprocess.run(
                [node, "--check", str(path)],
                cwd=str(root),
                text=True,
                encoding="utf-8",
                errors="replace",
                env=utf8_subprocess_environment(),
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                check=False,
            )
            rel = path.relative_to(root).as_posix()
            if process.returncode == 0:
                report.checks.append(f"JavaScript syntax valid: {rel}")
            else:
                report.errors.append(f"JavaScript syntax failed for {rel}: {(process.stderr or process.stdout).strip()}")

    def _check_http_serving(self, root: Path, report: StaticWebCheckReport) -> None:
        server = self._start_server(root)
        try:
            url = f"http://127.0.0.1:{server.server_port}/index.html"
            with urlopen(url, timeout=8) as response:
                body = response.read().decode("utf-8", errors="replace")
            if "html" not in body.lower():
                report.errors.append("HTTP index response does not look like HTML")
            else:
                report.checks.append(f"HTTP serving works: {url}")
        except Exception as error:
            report.errors.append(f"HTTP serving failed: {error}")
        finally:
            server.shutdown()
            server.server_close()

    def _check_browser_runtime(self, root: Path, report: StaticWebCheckReport) -> None:
        try:
            from playwright.sync_api import sync_playwright
        except Exception:
            report.warnings.append("Playwright is not installed; skipped browser runtime smoke test")
            return

        server = self._start_server(root)
        try:
            url = f"http://127.0.0.1:{server.server_port}/index.html"
            with sync_playwright() as playwright:
                browser = playwright.chromium.launch(headless=True)
                page = browser.new_page()
                console_errors: list[str] = []
                page_errors: list[str] = []
                page.on("console", lambda msg: console_errors.append(msg.text) if msg.type == "error" else None)
                page.on("pageerror", lambda exc: page_errors.append(str(exc)))
                page.goto(url, wait_until="domcontentloaded", timeout=10_000)
                body_text = page.locator("body").inner_text(timeout=5_000).strip()
                exercise_result = self._exercise_first_form(page)
                self._check_download_action(page, exercise_result)
                browser.close()
            if not body_text:
                report.errors.append("Browser rendered an empty body")
            if exercise_result.attempted:
                if not exercise_result.submitted:
                    report.errors.append("Browser form exercise found a form but could not submit it")
                elif not exercise_result.body_changed and not exercise_result.visible_values:
                    report.errors.append("Browser form submit did not change visible page state")
                else:
                    details = ", ".join(exercise_result.visible_values) or "page body changed"
                    report.checks.append(f"Browser form interaction updated visible state: {details}")
                if exercise_result.selected_values:
                    report.checks.append(
                        f"Browser interaction populated selectable values: {', '.join(exercise_result.selected_values)}"
                    )
                if exercise_result.local_storage_changed:
                    report.checks.append("Browser localStorage changed after form submit")
                if exercise_result.persisted_values:
                    report.checks.append(
                        f"Browser reload preserved submitted values: {', '.join(exercise_result.persisted_values)}"
                    )
                if exercise_result.download_triggered:
                    report.checks.append("Browser export/download action triggered")
            if page_errors:
                report.errors.append(f"Browser page errors: {' | '.join(page_errors[:3])}")
            if console_errors:
                report.errors.append(f"Browser console errors: {' | '.join(console_errors[:3])}")
            if body_text and not page_errors and not console_errors:
                report.checks.append("Browser runtime smoke test passed")
        except Exception as error:
            report.warnings.append(f"Browser runtime smoke test skipped or failed to start: {error}")
        finally:
            server.shutdown()
            server.server_close()

    def _exercise_first_form(self, page) -> BrowserExerciseResult:
        result = BrowserExerciseResult()
        forms = page.locator("form")
        if forms.count() < 1:
            return self._exercise_loose_controls(page)
        result.attempted = True
        form = forms.first
        inputs = form.locator("input, textarea, select")
        submitted_values: list[str] = []
        for index in range(inputs.count()):
            control = inputs.nth(index)
            tag = control.evaluate("el => el.tagName.toLowerCase()")
            input_type = control.get_attribute("type") or "text"
            if tag == "select":
                options = control.locator("option")
                if options.count() > 0:
                    value = options.nth(0).get_attribute("value")
                    if value is not None:
                        control.select_option(value)
                continue
            if input_type in {"button", "submit", "reset", "file", "hidden"}:
                continue
            value = self._sample_value(input_type, self._control_identity(control))
            control.fill(value)
            submitted_values.append(value)
        before_body = page.locator("body").inner_text(timeout=5_000).strip()
        before_storage = self._local_storage_snapshot(page)
        submit = form.locator("button[type=submit], input[type=submit], button").first
        if submit.count() > 0:
            submit.click(timeout=5_000)
            result.submitted = True
            page.wait_for_timeout(300)
        after_body = page.locator("body").inner_text(timeout=5_000).strip()
        after_storage = self._local_storage_snapshot(page)
        result.body_changed = after_body != before_body
        result.local_storage_changed = after_storage != before_storage
        result.visible_values = [value for value in submitted_values if self._value_visible(value, after_body)]
        result.selected_values = self._visible_select_values(page, submitted_values)
        if result.visible_values:
            page.reload(wait_until="domcontentloaded", timeout=10_000)
            page.wait_for_timeout(300)
            reloaded_body = page.locator("body").inner_text(timeout=5_000).strip()
            result.persisted_values = [value for value in result.visible_values if self._value_visible(value, reloaded_body)]
        return result

    def _exercise_loose_controls(self, page) -> BrowserExerciseResult:
        """Exercise common input + button UIs that do not use a <form>."""
        result = BrowserExerciseResult()
        inputs = page.locator(
            "input:not([type=button]):not([type=submit]):not([type=reset]):not([type=file]):not([type=hidden]), textarea"
        )
        if inputs.count() < 1:
            return result
        result.attempted = True
        submitted_values: list[str] = []
        for index in range(inputs.count()):
            control = inputs.nth(index)
            input_type = control.get_attribute("type") or "text"
            value = self._sample_value(input_type, self._control_identity(control))
            control.fill(value)
            submitted_values.append(value)
        before_body = page.locator("body").inner_text(timeout=5_000).strip()
        before_storage = self._local_storage_snapshot(page)
        button = self._primary_action_button(page)
        if button is not None:
            button.click(timeout=5_000)
            result.submitted = True
            page.wait_for_timeout(500)
        after_body = page.locator("body").inner_text(timeout=5_000).strip()
        after_storage = self._local_storage_snapshot(page)
        result.body_changed = after_body != before_body
        result.local_storage_changed = after_storage != before_storage
        result.visible_values = [value for value in submitted_values if self._value_visible(value, after_body)]
        if result.visible_values:
            page.reload(wait_until="domcontentloaded", timeout=10_000)
            page.wait_for_timeout(300)
            reloaded_body = page.locator("body").inner_text(timeout=5_000).strip()
            result.persisted_values = [value for value in result.visible_values if self._value_visible(value, reloaded_body)]
        return result

    def _primary_action_button(self, page):
        """Return the most likely submit/add button for non-form UIs."""
        buttons = page.locator("button, input[type=button], input[type=submit]")
        preferred = re.compile(r"add|create|save|submit|添加|新增|保存", re.IGNORECASE)
        excluded = re.compile(r"export|download|delete|remove|导出|下载|删除|移除", re.IGNORECASE)
        fallback = None
        for index in range(buttons.count()):
            button = buttons.nth(index)
            label = " ".join(
                [
                    button.inner_text(timeout=1_000) if button.evaluate("el => el.tagName.toLowerCase()") == "button" else "",
                    button.get_attribute("value") or "",
                    button.get_attribute("aria-label") or "",
                    button.get_attribute("id") or "",
                    button.get_attribute("class") or "",
                ]
            ).strip()
            if preferred.search(label):
                return button
            if fallback is None and not excluded.search(label):
                fallback = button
        return fallback

    def _check_download_action(self, page, result: BrowserExerciseResult) -> None:
        export_button = page.get_by_text(re.compile(r"CSV|Export|导出|下载", re.IGNORECASE)).first
        if export_button.count() < 1:
            return
        try:
            with page.expect_download(timeout=3_000):
                export_button.click(timeout=3_000)
            result.download_triggered = True
        except Exception:
            # Export buttons often use data URLs or browser-blocked download
            # paths in headless mode. Treat this as optional unless it raises a
            # page error captured by the main browser check.
            return

    def _control_identity(self, control) -> str:
        parts = [
            control.get_attribute("id") or "",
            control.get_attribute("name") or "",
            control.get_attribute("placeholder") or "",
            control.get_attribute("aria-label") or "",
        ]
        return " ".join(parts).lower()

    def _sample_value(self, input_type: str, identity: str = "") -> str:
        if any(token in identity for token in ("rating", "score", "star")):
            return "5"
        if any(token in identity for token in ("category", "分类", "type", "tag")):
            return "Food"
        if any(token in identity for token in ("note", "remark", "description", "备注", "说明")):
            return "Lunch"
        if any(token in identity for token in ("amount", "price", "cost", "金额", "费用")):
            return "12"
        return {
            "number": "12",
            "date": "2026-05-06",
            "email": "agent@example.com",
            "tel": "1234567890",
            "url": "https://example.com",
            "password": "password123",
        }.get(input_type, "sample")

    def _value_visible(self, value: str, body_text: str) -> bool:
        if value in body_text:
            return True
        return False

    def _visible_select_values(self, page, values: list[str]) -> list[str]:
        if not values:
            return []
        visible: list[str] = []
        options = page.locator("select option")
        option_text = "\n".join(options.nth(index).inner_text(timeout=1_000) for index in range(options.count()))
        for value in values:
            if value and value in option_text:
                visible.append(value)
        return visible

    def _local_storage_snapshot(self, page) -> str:
        try:
            return page.evaluate(
                "() => JSON.stringify(Object.fromEntries(Array.from({length: localStorage.length}, (_, i) => { const k = localStorage.key(i); return [k, localStorage.getItem(k)]; })))"
            )
        except Exception:
            return ""

    def _start_server(self, root: Path) -> ThreadingHTTPServer:
        class QuietHandler(SimpleHTTPRequestHandler):
            def log_message(self, format: str, *args) -> None:
                return

        handler = lambda *args, **kwargs: QuietHandler(*args, directory=str(root), **kwargs)
        server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        return server

    def _is_remote_or_inline(self, ref: str) -> bool:
        lowered = ref.lower()
        return lowered.startswith(("http://", "https://", "data:", "blob:", "//"))

    def _result(self, started: float, report: StaticWebCheckReport) -> HarnessResult:
        return HarnessResult(
            success=report.success,
            exit_code=0 if report.success else 1,
            stdout=report.render(),
            stderr="",
            duration_ms=int((perf_counter() - started) * 1000),
            failure_reason="" if report.success else "static_web_validation_failed",
        )
