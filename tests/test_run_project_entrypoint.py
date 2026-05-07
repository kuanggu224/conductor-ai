"""Tests for the UTF-8 project runner entrypoint."""

from app.run_project import _resolve_project_root, build_parser


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
            "--diagnose-llm",
        ]
    )

    assert args.collaboration_max_rounds == 1
    assert args.static_requirement_review is True
    assert args.diagnose_llm is True
