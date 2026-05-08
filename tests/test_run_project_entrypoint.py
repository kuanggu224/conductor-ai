"""Tests for the UTF-8 project runner entrypoint."""

import json
from types import SimpleNamespace

from app import run_project
from app.run_project import _build_cli_config, _run_preflight_gate, _resolve_project_root, build_parser
from conductor.agents.llm import LLMHTTPConfig
from conductor.config.execution import resolve_run_profile
from conductor.config.llm import LLMRuntimeConfig, LLMUsagePolicy


def test_run_project_parser_accepts_requirement_file() -> None:
    args = build_parser().parse_args(["--requirement-file", "requirement.txt", "--project-root", "demo"])

    assert args.requirement_file == "requirement.txt"
    assert args.project_root == "demo"
    assert args.codex is False
    assert args.run_profile == "mock"


def test_run_project_parser_accepts_project_name() -> None:
    args = build_parser().parse_args(["--requirement", "demo", "--project-root", "C:/tmp/conductor_test", "--project-name", "case-1"])

    assert args.project_name == "case-1"


def test_resolve_project_root_uses_child_for_conductor_test_root() -> None:
    resolved = _resolve_project_root("C:/tmp/conductor_test", "case-1")

    assert resolved.as_posix().endswith("/conductor_test/case-1")


def test_run_project_parser_accepts_run_profile() -> None:
    args = build_parser().parse_args(["--requirement", "demo", "--run-profile", "full_cli", "--codex"])

    assert args.run_profile == "full_cli"
    assert args.codex is True


def test_run_project_parser_accepts_agent_cli() -> None:
    args = build_parser().parse_args(["--requirement", "demo", "--run-profile", "design_cli_only", "--agent-cli", "opencode"])

    assert args.run_profile == "design_cli_only"
    assert args.agent_cli == "opencode"


def test_run_project_parser_accepts_aspirecode_agent_cli() -> None:
    args = build_parser().parse_args(
        [
            "--requirement",
            "demo",
            "--run-profile",
            "design_cli_only",
            "--agent-cli",
            "aspirecode",
            "--aspirecode-model",
            "lmstudio-local/qwen3.6-35b-a3b",
        ]
    )

    assert args.agent_cli == "aspirecode"
    assert args.aspirecode_model == "lmstudio-local/qwen3.6-35b-a3b"


def test_run_project_parser_accepts_llm_harness() -> None:
    args = build_parser().parse_args(
        [
            "--requirement",
            "demo",
            "--run-profile",
            "design_cli_only",
            "--llm-harness",
            "local",
            "--llm-base-url",
            "http://127.0.0.1:1234/v1",
            "--llm-model",
            "qwen3.6-35b-a3b",
            "--llm-reasoning-effort",
            "none",
        ]
    )

    assert args.llm_harness == "local"
    assert args.llm_base_url == "http://127.0.0.1:1234/v1"
    assert args.llm_model == "qwen3.6-35b-a3b"
    assert args.llm_reasoning_effort == "none"


def test_run_project_parser_accepts_collaboration_overrides() -> None:
    args = build_parser().parse_args(
        [
            "--requirement",
            "demo",
            "--collaboration-max-rounds",
            "1",
            "--static-requirement-review",
            "--diagnose-cli",
            "--diagnose-llm",
        ]
    )

    assert args.collaboration_max_rounds == 1
    assert args.static_requirement_review is True
    assert args.diagnose_cli is True
    assert args.diagnose_llm is True


def test_run_project_parser_accepts_skip_preflight_gate() -> None:
    args = build_parser().parse_args(["--requirement", "demo", "--skip-preflight-gate"])

    assert args.skip_preflight_gate is True


def test_preflight_gate_blocks_real_profile_without_backend(tmp_path) -> None:
    run_profile = resolve_run_profile("design_cli_only")
    llm_runtime_config = LLMRuntimeConfig(
        local=LLMHTTPConfig(base_url="http://127.0.0.1:1234/v1", model_name="local-model", enabled=False),
        cloud=LLMHTTPConfig(base_url="https://example.com/v1", model_name="cloud-model", enabled=False),
        usage=LLMUsagePolicy(runner_enabled=False),
    )

    payload = _run_preflight_gate(
        cli_config=_build_cli_config(None, run_profile),
        llm_runtime_config=llm_runtime_config,
        project_root=tmp_path,
        run_profile=run_profile,
        agent_cli=None,
        llm_harness_backend=None,
    )

    assert payload["ok"] is False
    assert "requires real outputs" in payload["preflight_gate"]["errors"][0]
    diagnostics_path = tmp_path / ".conductor" / "diagnostics" / "run-preflight" / "preflight-gate.json"
    persisted = json.loads(diagnostics_path.read_text(encoding="utf-8"))
    assert persisted["ok"] is False
    assert persisted["preflight_gate"]["diagnostics_path"] == str(diagnostics_path)
    assert payload["preflight_gate"]["diagnostics_path"] == str(diagnostics_path)


def test_preflight_gate_skips_mock_run_even_when_llm_runner_is_configured(tmp_path) -> None:
    run_profile = resolve_run_profile("mock")
    llm_runtime_config = LLMRuntimeConfig(
        local=LLMHTTPConfig(base_url="http://127.0.0.1:1234/v1", model_name="local-model", enabled=True),
        cloud=LLMHTTPConfig(base_url="https://example.com/v1", model_name="cloud-model", enabled=True),
        usage=LLMUsagePolicy(runner_enabled=True),
    )

    payload = _run_preflight_gate(
        cli_config=_build_cli_config(None, run_profile),
        llm_runtime_config=llm_runtime_config,
        project_root=tmp_path,
        run_profile=run_profile,
        agent_cli=None,
        llm_harness_backend=None,
    )

    assert payload == {"ok": True, "skipped": True, "reason": "mock_or_offline_run"}


def test_preflight_gate_allows_ready_llm_harness(monkeypatch, tmp_path) -> None:
    run_profile = resolve_run_profile("design_cli_only")
    llm_runtime_config = LLMRuntimeConfig(
        local=LLMHTTPConfig(base_url="http://127.0.0.1:1234/v1", model_name="local-model", enabled=True),
        cloud=LLMHTTPConfig(base_url="https://example.com/v1", model_name="cloud-model", enabled=False),
        usage=LLMUsagePolicy(runner_enabled=False),
    )

    monkeypatch.setattr(run_project, "build_platform_diagnostics", lambda **_: fake_diagnostics("local", "ready"))

    payload = _run_preflight_gate(
        cli_config=_build_cli_config(None, run_profile),
        llm_runtime_config=llm_runtime_config,
        project_root=tmp_path,
        run_profile=run_profile,
        agent_cli=None,
        llm_harness_backend="local",
    )

    assert payload["ok"] is True
    assert (tmp_path / ".conductor" / "diagnostics" / "run-preflight" / "preflight-gate.json").exists()


def test_preflight_gate_blocks_failed_llm_harness(monkeypatch, tmp_path) -> None:
    run_profile = resolve_run_profile("design_cli_only")
    llm_runtime_config = LLMRuntimeConfig(
        local=LLMHTTPConfig(base_url="http://127.0.0.1:1234/v1", model_name="local-model", enabled=True),
        cloud=LLMHTTPConfig(base_url="https://example.com/v1", model_name="cloud-model", enabled=False),
        usage=LLMUsagePolicy(runner_enabled=False),
    )

    monkeypatch.setattr(
        run_project,
        "build_platform_diagnostics",
        lambda **_: fake_diagnostics("local", "failed", warnings=["local LLM preflight failed"]),
    )

    payload = _run_preflight_gate(
        cli_config=_build_cli_config(None, run_profile),
        llm_runtime_config=llm_runtime_config,
        project_root=tmp_path,
        run_profile=run_profile,
        agent_cli=None,
        llm_harness_backend="local",
    )

    assert payload["ok"] is False
    assert any("preflight failed" in error for error in payload["preflight_gate"]["errors"])


def fake_diagnostics(backend: str, health_status: str, warnings: list[str] | None = None):
    return SimpleNamespace(
        warnings=warnings or [],
        llm_backends=[
            SimpleNamespace(
                backend=backend,
                health_status=health_status,
                recommendation="diagnostic recommendation",
            )
        ],
        to_dict=lambda: {"llm_backends": [{"backend": backend, "health_status": health_status}]},
    )
