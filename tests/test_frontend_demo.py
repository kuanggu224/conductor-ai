"""Smoke tests for the frontend offline demo fixture."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest


def test_frontend_demo_fixture_exposes_stable_progression(tmp_path: Path) -> None:
    node = shutil.which("node")
    if node is None:
        pytest.skip("node is not available")

    repo_root = Path(__file__).resolve().parents[1]
    demo_path = repo_root / "frontend" / "src" / "demo.js"
    script_path = tmp_path / "check-demo.mjs"
    script_path.write_text(
        """
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";

const source = readFileSync(process.argv[2], "utf8");
const moduleUrl = `data:text/javascript;base64,${Buffer.from(source).toString("base64")}`;
const demo = await import(moduleUrl);

const projects = demo.demoProjects();
assert.equal(projects.length, 1);
assert.equal(projects[0].project_id, "demo-api-delivery");

const initial = demo.createDemoProject();
assert.equal(initial.snapshot.demo_step, 0);
assert.equal(initial.snapshot.project_status, "in_progress");
assert.equal(initial.snapshot.run_audit.delivery_readiness_score, 78);
assert.equal(initial.snapshot.task_center_summary.claimable, 1);

const stepped = demo.advanceDemoProject(initial, "step");
assert.equal(stepped.snapshot.demo_step, 1);
assert.equal(stepped.snapshot.current_stage, "testing");
assert.equal(stepped.snapshot.run_audit.delivery_readiness_score, 92);
assert.equal(stepped.snapshot.blockers.length, 0);

const completed = demo.advanceDemoProject(stepped, "run");
assert.equal(completed.snapshot.demo_step, 2);
assert.equal(completed.snapshot.project_status, "ready");
assert.equal(completed.snapshot.run_audit.delivery_readiness_score, 96);
assert.equal(completed.snapshot.operation_console.actions.find((action) => action.id === "approve").enabled, true);

assert.match(demo.demoArtifact("artifact-api-implementation").content, /pytest.ini/);
assert.equal(demo.demoTaskContext("assignment-api-validation").workitem_id, "workitem-validation");
assert.equal(demo.demoOperationResult("Sweep").mode, "demo");
assert.equal(demo.demoTodos().todos.length, 3);
assert.equal(demo.demoTodoStats().total, 3);
assert.equal(demo.demoTodoDetail(1).todo.title, "验证 API 契约");
assert.equal(demo.demoSettingsPayload().execution.config.run_profile, "api_sqlite");
const capabilities = demo.demoCapabilityMatrix();
assert.equal(capabilities.length, 6);
assert.equal(capabilities.find((item) => item.area === "任务中心").status, "已对齐");
assert.match(capabilities.find((item) => item.area === "外部执行").live, /LLM/);
assert.equal(demo.demoDiagnostics(true).backend_required, false);
assert.equal(demo.demoLlmPreflight().checks.find((check) => check.name === "model_call").status, "skipped");
""".strip(),
        encoding="utf-8",
    )
    result = subprocess.run(
        [node, str(script_path), str(demo_path)],
        cwd=repo_root,
        text=True,
        encoding="utf-8",
        capture_output=True,
        timeout=20,
    )
    assert result.returncode == 0, result.stderr or result.stdout
