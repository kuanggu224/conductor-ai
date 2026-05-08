"""Requirement benchmark CLI tests."""

import json
from pathlib import Path

import pytest

import app.requirement_benchmark as requirement_benchmark_cli
from app.requirement_benchmark import main
from conductor.requirement_benchmark import DirectRequirementBaselineResult
from conductor.requirement_benchmark import RequirementLLMPreflightResult


def test_requirement_benchmark_cli_scores_document(tmp_path, capsys) -> None:
    document = tmp_path / "requirement.md"
    document.write_text(
        "目标：个人读书清单。范围：书名、作者、阅读状态、评分、备注、状态筛选、CSV 导出、刷新后保留数据。"
        "验收标准：新增、筛选、导出、刷新保留。风险：CSV 编码。测试：验证新增、筛选、持久化。",
        encoding="utf-8",
    )

    exit_code = main(["score", "--case", "reading_list", "--document-file", str(document)])

    captured = capsys.readouterr()
    assert exit_code == 0
    assert '"score"' in captured.out


def test_requirement_benchmark_cli_compares_documents(tmp_path) -> None:
    platform = tmp_path / "platform.md"
    direct = tmp_path / "direct.md"
    output_dir = tmp_path / "out"
    platform.write_text(
        "目标：个人读书清单。范围：书名、作者、阅读状态、评分、备注、状态筛选、CSV 导出、刷新后保留数据。"
        "非目标：登录。页面和数据：表单、列表、localStorage。验收标准：新增、筛选、导出、刷新保留。"
        "风险：CSV 编码。测试：验证新增、筛选、持久化、导出。",
        encoding="utf-8",
    )
    direct.write_text("做一个读书清单，包含书名和作者。", encoding="utf-8")

    exit_code = main(
        [
            "compare",
            "--case",
            "reading_list",
            "--platform-file",
            str(platform),
            "--direct-file",
            str(direct),
            "--output-dir",
            str(output_dir),
        ]
    )

    assert exit_code == 0
    assert (output_dir / "requirement-comparison-results.json").exists()
    assert (output_dir / "requirement-comparison-results.md").exists()


def test_requirement_benchmark_cli_can_generate_direct_llm_baseline(tmp_path, monkeypatch) -> None:
    platform = tmp_path / "platform.md"
    output_dir = tmp_path / "out"
    platform.write_text(
        "目标：个人读书清单。范围：书名、作者、阅读状态、评分、备注、状态筛选、CSV 导出、刷新后保留数据。"
        "非目标：登录。页面和数据：表单、列表、localStorage。验收标准：新增、筛选、导出、刷新保留。"
        "风险：CSV 编码。测试：验证新增、筛选、持久化、导出。",
        encoding="utf-8",
    )

    def fake_direct_baseline(case, *, output_dir, config, prompt_mode="plain"):
        direct_path = Path(output_dir) / "reading_list.direct-requirement.md"
        direct_path.parent.mkdir(parents=True, exist_ok=True)
        direct_path.write_text("目标：读书清单。范围：书名、作者。", encoding="utf-8")
        return DirectRequirementBaselineResult(
            case_id=case.id,
            content=direct_path.read_text(encoding="utf-8"),
            output_path=str(direct_path),
            model="fake-direct",
            duration_ms=1,
        )

    monkeypatch.setattr(requirement_benchmark_cli, "run_direct_requirement_baseline", fake_direct_baseline)

    exit_code = main(
        [
            "compare",
            "--case",
            "reading_list",
            "--platform-file",
            str(platform),
            "--direct-llm",
            "local",
            "--output-dir",
            str(output_dir),
        ]
    )

    assert exit_code == 0
    assert (output_dir / "direct" / "reading_list.direct-requirement.md").exists()


def test_requirement_benchmark_cli_runs_suite_from_directories(tmp_path) -> None:
    platform_dir = tmp_path / "platform"
    direct_dir = tmp_path / "direct"
    output_dir = tmp_path / "suite-out"
    platform_dir.mkdir()
    direct_dir.mkdir()
    strong = (
        "目标：Reading list. 范围：book title, author, status, rating, CSV export, persistence, filtering. "
        "非目标：login. 页面和数据：form, list, localStorage. "
        "验收标准：add, filter, export, refresh keeps data. 风险：CSV encoding. 测试：verify add/filter/export/storage."
    )
    weak = "Build a reading list with title and author."
    (platform_dir / "reading_list.platform.md").write_text(strong, encoding="utf-8")
    (direct_dir / "reading_list.direct-requirement.md").write_text(weak, encoding="utf-8")

    exit_code = main(
        [
            "suite",
            "--cases",
            "reading_list",
            "--platform-dir",
            str(platform_dir),
            "--direct-dir",
            str(direct_dir),
            "--output-dir",
            str(output_dir),
        ]
    )

    assert exit_code == 0
    assert (output_dir / "requirement-comparison-results.json").exists()
    assert (output_dir / "requirement-comparison-results.md").exists()


def test_requirement_benchmark_cli_runs_generated_suite(tmp_path, monkeypatch) -> None:
    direct_dir = tmp_path / "direct"
    output_dir = tmp_path / "generated-suite"
    direct_dir.mkdir()
    (direct_dir / "reading_list.direct-requirement.md").write_text(
        "Build a reading list with title and author.",
        encoding="utf-8",
    )

    def fake_platform_case(
        *,
        case,
        output_dir,
        run_profile,
        max_steps,
        platform_llm,
        llm_overrides=None,
        collaboration_max_rounds=None,
        dynamic_requirement_review_enabled=True,
    ):
        assert collaboration_max_rounds == 1
        assert dynamic_requirement_review_enabled is False
        return {
            "case_id": case.id,
            "project_id": "project-generated",
            "project_root": str(Path(output_dir) / case.id),
            "manifest_path": str(Path(output_dir) / case.id / ".conductor" / "manifests" / "project-generated.manifest.json"),
            "report_path": str(Path(output_dir) / case.id / ".conductor" / "reports" / "project-generated.md"),
            "current_stage": "design",
            "status": "in_progress",
            "steps": 1,
            "document": (
                "目标：Reading list. 范围：book title, author, status, rating, CSV export, persistence, filtering. "
                "非目标：login. 页面和数据：form, list, localStorage. "
                "验收标准：add, filter, export, refresh keeps data. 风险：CSV encoding. "
                "测试：verify add/filter/export/storage."
            ),
        }

    monkeypatch.setattr(requirement_benchmark_cli, "_run_platform_requirement_case", fake_platform_case)

    exit_code = main(
        [
            "run-suite",
            "--cases",
            "reading_list",
            "--output-dir",
            str(output_dir),
            "--direct-dir",
            str(direct_dir),
            "--collaboration-max-rounds",
            "1",
            "--static-requirement-review",
        ]
    )

    assert exit_code == 0
    assert (output_dir / "requirement-comparison-results.json").exists()
    assert (output_dir / "requirement-generated-suite.json").exists()
    payload_text = (output_dir / "requirement-generated-suite.json").read_text(encoding="utf-8")
    payload = json.loads(payload_text)
    assert "platform_runs" in payload
    assert "direct_runs" in payload
    assert payload["run_config"]["requirement_review_mode"] == "static"
    assert payload["run_config"]["dynamic_requirement_review_enabled"] is False
    assert payload["platform_runs"][0]["requirement_review_mode"] == "static"
    assert payload["platform_runs"][0]["dynamic_requirement_review_enabled"] is False
    assert payload["direct_runs"][0]["source"] == "file"
    assert '"document"' not in payload_text


def test_requirement_benchmark_cli_reports_generated_suite_direct_failure(tmp_path, monkeypatch) -> None:
    output_dir = tmp_path / "generated-suite-error"

    def fake_platform_case(
        *,
        case,
        output_dir,
        run_profile,
        max_steps,
        platform_llm,
        llm_overrides=None,
        collaboration_max_rounds=None,
        dynamic_requirement_review_enabled=True,
    ):
        return {
            "case_id": case.id,
            "project_id": "project-generated",
            "project_root": str(Path(output_dir) / case.id),
            "manifest_path": "",
            "report_path": "",
            "current_stage": "design",
            "status": "in_progress",
            "steps": 1,
            "document": "目标：Reading list. 范围：book title, author, status, rating, CSV export, persistence, filtering. "
            "非目标：login. 验收标准：add/filter/export. 风险：CSV. 测试：verify behavior.",
        }

    def fake_direct_baseline(*args, **kwargs):
        raise RuntimeError("connection refused")

    monkeypatch.setattr(requirement_benchmark_cli, "_run_platform_requirement_case", fake_platform_case)
    monkeypatch.setattr(requirement_benchmark_cli, "run_direct_requirement_baseline", fake_direct_baseline)

    with pytest.raises(SystemExit) as error:
        main(
            [
                "run-suite",
                "--cases",
                "reading_list",
                "--output-dir",
                str(output_dir),
                "--direct-llm",
                "local",
                "--skip-preflight",
            ]
        )

    assert "Direct requirement baseline failed for reading_list" in str(error.value)


def test_requirement_benchmark_cli_preflight_reports_status(tmp_path, monkeypatch, capsys) -> None:
    def fake_preflight(*, backend, config, output_dir):
        return RequirementLLMPreflightResult(
            backend=backend,
            success=True,
            model=config.model_name,
            base_url=config.base_url,
            duration_ms=1,
            content="ok",
        )

    monkeypatch.setattr(requirement_benchmark_cli, "run_requirement_llm_preflight", fake_preflight)

    exit_code = main(
        [
            "preflight",
            "--backend",
            "local",
            "--output-dir",
            str(tmp_path),
            "--llm-model",
            "override-model",
            "--llm-base-url",
            "http://127.0.0.1:9999/v1",
        ]
    )

    captured = capsys.readouterr()
    assert exit_code == 0
    assert '"success": true' in captured.out
    assert '"model": "override-model"' in captured.out
    assert '"base_url": "http://127.0.0.1:9999/v1"' in captured.out


def test_requirement_benchmark_cli_run_suite_preflight_fails_fast(tmp_path, monkeypatch) -> None:
    def fake_preflight(*, backend, config, output_dir):
        return RequirementLLMPreflightResult(
            backend=backend,
            success=False,
            model=config.model_name,
            base_url=config.base_url,
            duration_ms=1,
            error="connection refused",
        )

    monkeypatch.setattr(requirement_benchmark_cli, "run_requirement_llm_preflight", fake_preflight)

    with pytest.raises(SystemExit) as error:
        main(
            [
                "run-suite",
                "--cases",
                "reading_list",
                "--output-dir",
                str(tmp_path),
                "--platform-llm",
                "local",
                "--direct-llm",
                "local",
            ]
        )

    assert "LLM preflight failed for local" in str(error.value)
