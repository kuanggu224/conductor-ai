"""Consistency checks for demo documentation and fixture data."""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).resolve().parents[1]


def test_demo_docs_use_the_demo_mode_url() -> None:
    docs = [
        REPO_ROOT / "README.md",
        REPO_ROOT / "frontend" / "README.md",
        REPO_ROOT / "frontend" / "DEMO_SCRIPT.md",
    ]
    expected_url = "http://127.0.0.1:4176/?demo=1"

    for doc in docs:
        text = doc.read_text(encoding="utf-8")
        assert expected_url in text, f"{doc.name} should document the demo mode URL"

    frontend_readme = (REPO_ROOT / "frontend" / "README.md").read_text(encoding="utf-8")
    offline_section = frontend_readme.split("Run the offline presentation demo:", 1)[1].split("The frontend uses", 1)[0]
    assert expected_url in offline_section
    assert "http://127.0.0.1:4176\n" not in offline_section


def test_demo_script_matches_fixture_story(tmp_path: Path) -> None:
    node = shutil.which("node")
    if node is None:
        pytest.skip("node is not available")

    demo_path = REPO_ROOT / "frontend" / "src" / "demo.js"
    script_path = tmp_path / "read-demo-story.mjs"
    script_path.write_text(
        """
import { readFileSync } from "node:fs";

const source = readFileSync(process.argv[2], "utf8");
const moduleUrl = `data:text/javascript;base64,${Buffer.from(source).toString("base64")}`;
const demo = await import(moduleUrl);

const initial = demo.createDemoProject();
const stepped = demo.advanceDemoProject(initial, "step");
const completed = demo.advanceDemoProject(stepped, "run");
const approve = completed.snapshot.operation_console.actions.find((action) => action.id === "approve");

process.stdout.write(JSON.stringify({
  initialStatus: initial.snapshot.project_status_label,
  initialRisk: initial.snapshot.run_audit.risk_level_label,
  initialReadiness: `${initial.snapshot.run_audit.delivery_readiness_status_label} / ${initial.snapshot.run_audit.delivery_readiness_score}`,
  initialClaimable: initial.snapshot.task_center_summary.claimable,
  initialBlocked: initial.snapshot.blockers.length,
  steppedRisk: stepped.snapshot.run_audit.risk_level_label,
  steppedReadiness: `${stepped.snapshot.run_audit.delivery_readiness_status_label} / ${stepped.snapshot.run_audit.delivery_readiness_score}`,
  steppedBlocked: stepped.snapshot.blockers.length,
  completedStatus: completed.snapshot.project_status_label,
  completedReadiness: `${completed.snapshot.run_audit.delivery_readiness_status_label} / ${completed.snapshot.run_audit.delivery_readiness_score}`,
  approveEnabled: approve?.enabled === true,
  artifactTitles: completed.snapshot.artifacts.map((artifact) => artifact.title),
}));
""".strip(),
        encoding="utf-8",
    )
    result = subprocess.run(
        [node, str(script_path), str(demo_path)],
        cwd=REPO_ROOT,
        text=True,
        capture_output=True,
        timeout=20,
    )
    assert result.returncode == 0, result.stderr or result.stdout

    story = json.loads(result.stdout)
    demo_script = (REPO_ROOT / "frontend" / "DEMO_SCRIPT.md").read_text(encoding="utf-8")

    assert f"Top status is `{story['initialStatus']}`" in demo_script
    assert f"Risk panel shows `{story['initialRisk']}`, `{story['initialReadiness']}`, `Claimable {story['initialClaimable']}`, `Blocked {story['initialBlocked']}`" in demo_script
    assert f"readiness moves to `{story['steppedReadiness']}`" in demo_script
    assert f"risk becomes `{story['steppedRisk']}`" in demo_script
    assert f"blockers become `{story['steppedBlocked']}`" in demo_script
    assert f"project status becomes `{story['completedStatus']}`" in demo_script
    assert f"readiness becomes `{story['completedReadiness']}`" in demo_script
    assert story["approveEnabled"] is True
    assert "`Approve` becomes enabled" in demo_script
    for title in ["FastAPI SQLite Implementation", "API Contract Validation"]:
        assert title in story["artifactTitles"]
        assert title in demo_script


def test_demo_script_documents_offline_boundaries() -> None:
    demo_script = (REPO_ROOT / "frontend" / "DEMO_SCRIPT.md").read_text(encoding="utf-8")
    frontend_readme = (REPO_ROOT / "frontend" / "README.md").read_text(encoding="utf-8")

    assert "Demo mode is deterministic and offline" in demo_script
    assert "does not require the backend, Agent CLI, or external LLM provider" in demo_script
    assert "Live mode should be used only when the backend is running" in demo_script
    assert "demo-check.ps1 -StaticSmoke -FullBackendChecks" in demo_script
    assert "demo-start.ps1` runs the same frontend preflight" in demo_script
    assert "-PreflightSmokePort 4179" in demo_script
    assert "`Check Backend` calls `/api/status`" in demo_script
    assert "expect `Online` before running `Step` or `Run`" in demo_script
    assert "including static asset smoke checks" in frontend_readme
    assert "-PreflightSmokePort 4179" in frontend_readme
    assert "use `Check Backend` to confirm the configured API URL responds" in frontend_readme
