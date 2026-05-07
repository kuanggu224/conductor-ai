"""Tests for the controlled LLM harness."""

from pathlib import Path

from conductor.agents.llm import LLMHTTPConfig
from conductor.harness.llm import LLMHarnessRequest, OpenAICompatibleLLMHarness


class FakeLLMHarness(OpenAICompatibleLLMHarness):
    def __init__(self, payload):
        self.payload = payload

    def _post_json(self, config, path, payload):
        self.last_config = config
        self.last_path = path
        self.last_payload = payload
        return self.payload


def test_llm_harness_writes_controlled_output_file(tmp_path) -> None:
    harness = FakeLLMHarness(
        {
            "choices": [
                {
                    "message": {
                        "content": "# 目标\n真实设计\n\n## 验收标准\n- 可验证",
                    }
                }
            ]
        }
    )

    result = harness.run(
        LLMHarnessRequest(
            prompt="生成设计",
            system_prompt="system",
            working_directory=str(tmp_path),
            output_path=".conductor/llm_outputs/workitem-001.md",
            config=LLMHTTPConfig(
                base_url="http://127.0.0.1:1234/v1",
                model_name="qwen-local",
                enabled=True,
            ),
        )
    )

    output_path = tmp_path / ".conductor" / "llm_outputs" / "workitem-001.md"
    assert result.success is True
    assert result.output_path == str(output_path.resolve())
    assert output_path.read_text(encoding="utf-8").startswith("# 目标")
    assert harness.last_path == "/chat/completions"
    assert harness.last_payload["messages"][0]["role"] == "system"


def test_llm_harness_rejects_output_path_outside_workspace(tmp_path) -> None:
    harness = FakeLLMHarness({"choices": [{"message": {"content": "content"}}]})

    result = harness.run(
        LLMHarnessRequest(
            prompt="生成设计",
            working_directory=str(tmp_path),
            output_path="../escape.md",
            config=LLMHTTPConfig(
                base_url="http://127.0.0.1:1234/v1",
                model_name="qwen-local",
                enabled=True,
            ),
        )
    )

    assert result.success is False
    assert "escapes workspace" in result.error
    assert not (tmp_path.parent / "escape.md").exists()


def test_llm_harness_uses_reasoning_content_when_content_empty(tmp_path) -> None:
    harness = FakeLLMHarness({"choices": [{"message": {"content": "", "reasoning_content": "fallback"}}]})

    result = harness.run(
        LLMHarnessRequest(
            prompt="生成设计",
            working_directory=str(tmp_path),
            config=LLMHTTPConfig(
                base_url="http://127.0.0.1:1234/v1",
                model_name="qwen-local",
                enabled=True,
            ),
        )
    )

    assert result.success is True
    assert result.content == "fallback"


def test_llm_harness_disables_reasoning_for_qwen_models(tmp_path) -> None:
    harness = FakeLLMHarness({"choices": [{"message": {"content": "content"}}]})

    result = harness.run(
        LLMHarnessRequest(
            prompt="Generate a design document.",
            working_directory=str(tmp_path),
            config=LLMHTTPConfig(
                base_url="http://127.0.0.1:1234/v1",
                model_name="qwen3.6-35b-a3b",
                enabled=True,
            ),
        )
    )

    assert result.success is True
    assert harness.last_payload["messages"][-1]["content"] == "Generate a design document."
    assert harness.last_payload["reasoning_effort"] == "none"


def test_llm_harness_does_not_set_reasoning_effort_for_non_qwen_models(tmp_path) -> None:
    harness = FakeLLMHarness({"choices": [{"message": {"content": "content"}}]})

    result = harness.run(
        LLMHarnessRequest(
            prompt="Generate a design document.",
            working_directory=str(tmp_path),
            config=LLMHTTPConfig(
                base_url="http://127.0.0.1:1234/v1",
                model_name="glm-4.6",
                enabled=True,
            ),
        )
    )

    assert result.success is True
    assert harness.last_payload["messages"][-1]["content"] == "Generate a design document."
    assert "reasoning_effort" not in harness.last_payload


def test_llm_harness_uses_configured_reasoning_effort(tmp_path) -> None:
    harness = FakeLLMHarness({"choices": [{"message": {"content": "content"}}]})

    result = harness.run(
        LLMHarnessRequest(
            prompt="Generate a design document.",
            working_directory=str(tmp_path),
            config=LLMHTTPConfig(
                base_url="http://127.0.0.1:1234/v1",
                model_name="qwen3.6-35b-a3b",
                reasoning_effort="low",
                enabled=True,
            ),
        )
    )

    assert result.success is True
    assert harness.last_payload["reasoning_effort"] == "low"
