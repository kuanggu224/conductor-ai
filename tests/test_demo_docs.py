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
    offline_section = frontend_readme.split("运行离线演示：", 1)[1].split("前端默认使用", 1)[0]
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
        encoding="utf-8",
        capture_output=True,
        timeout=20,
    )
    assert result.returncode == 0, result.stderr or result.stdout

    story = json.loads(result.stdout)
    demo_script = (REPO_ROOT / "frontend" / "DEMO_SCRIPT.md").read_text(encoding="utf-8")

    assert f"顶部状态为 `{story['initialStatus']}`" in demo_script
    assert f"风险面板显示 `{story['initialRisk']}`、`{story['initialReadiness']}`、`可领取 {story['initialClaimable']}`、`阻塞 {story['initialBlocked']}`" in demo_script
    assert f"就绪度变为 `{story['steppedReadiness']}`" in demo_script
    assert f"风险变为 `{story['steppedRisk']}`" in demo_script
    assert f"阻塞项变为 `{story['steppedBlocked']}`" in demo_script
    assert f"项目状态变为 `{story['completedStatus']}`" in demo_script
    assert f"就绪度变为 `{story['completedReadiness']}`" in demo_script
    assert story["approveEnabled"] is True
    assert "`批准` 变为可用" in demo_script
    for title in ["FastAPI SQLite 实现", "API 契约验证"]:
        assert title in story["artifactTitles"]
        assert title in demo_script


def test_demo_script_documents_offline_boundaries() -> None:
    root_readme = (REPO_ROOT / "README.md").read_text(encoding="utf-8")
    demo_script = (REPO_ROOT / "frontend" / "DEMO_SCRIPT.md").read_text(encoding="utf-8")
    frontend_readme = (REPO_ROOT / "frontend" / "README.md").read_text(encoding="utf-8")

    assert "演示模式是确定性离线模式" in demo_script
    assert "不需要后端、Agent CLI 或外部 LLM 服务商" in demo_script
    assert "实时模式只应在后端运行于已配置 API URL 时使用" in demo_script
    assert "demo-check.ps1 -StaticSmoke -FullBackendChecks" in demo_script
    assert "demo-start.ps1` 会先运行相同的前端预检" in demo_script
    assert "-PreflightSmokePort 4179" in demo_script
    assert "`检查后端` 会调用 `/api/status`" in demo_script
    assert "运行 `推进` 或 `运行` 前预期状态为 `在线`" in demo_script
    assert "点击 `加载演示` 进入离线展示路径，或点击 `设置` 检查后端 URL" in demo_script
    assert "包括静态资源冒烟检查" in frontend_readme
    assert "-PreflightSmokePort 4179" in frontend_readme
    assert "使用 `检查后端` 确认已配置的 API URL 可响应" in frontend_readme
    assert "点击 `加载演示` 进入离线展示路径，或点击 `设置` 检查后端 URL" in root_readme
    assert "使用 `检查后端` 确认已配置的 API URL 可响应" in root_readme
    assert "start.bat" in root_readme
    assert "stop.bat" in root_readme
    assert "start.bat" in frontend_readme
    assert "stop.bat" in frontend_readme
