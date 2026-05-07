"""OpenAI-compatible LLM harness.

This harness is intentionally smaller than a coding agent. It asks a model for
one bounded text artifact, then the platform writes that artifact to disk and
validates the file boundary itself.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from time import perf_counter
from typing import Any

from conductor.agents.llm import LLMHTTPConfig
from conductor.harness.models import StreamCallback


@dataclass(slots=True)
class LLMHarnessRequest:
    """One controlled LLM artifact-generation request."""

    prompt: str
    working_directory: str
    config: LLMHTTPConfig
    output_path: str | None = None
    system_prompt: str = ""
    temperature: float = 0.2
    max_tokens: int = 4096
    metadata: dict[str, str] = field(default_factory=dict)
    stream_callback: StreamCallback | None = None


@dataclass(slots=True)
class LLMHarnessResult:
    """Result of one LLM harness run."""

    success: bool
    content: str
    duration_ms: int
    model_name: str
    output_path: str | None = None
    error: str = ""
    raw_response: dict[str, Any] | None = None


class OpenAICompatibleLLMHarness:
    """Call an OpenAI-compatible chat endpoint and write a controlled artifact."""

    name = "llm"

    def run(self, request: LLMHarnessRequest) -> LLMHarnessResult:
        """Generate one artifact and optionally write it below the workspace."""
        started = perf_counter()
        if not request.config.enabled:
            return self._failed(request, started, "LLM harness config is disabled")
        try:
            payload = self._build_payload(request)
            response_payload = self._post_json(request.config, "/chat/completions", payload)
            content = self._extract_content(response_payload).strip()
            if not content:
                return self._failed(request, started, "LLM response content is empty", response_payload)
            output_path = self._write_output_file(request, content)
            if request.stream_callback is not None:
                request.stream_callback("stdout", f"[llm-harness] generated {len(content)} chars\n")
            return LLMHarnessResult(
                success=True,
                content=content,
                duration_ms=self._duration_ms(started),
                model_name=request.config.model_name,
                output_path=str(output_path) if output_path else None,
                raw_response=response_payload,
            )
        except Exception as error:
            return self._failed(request, started, str(error))

    def _build_payload(self, request: LLMHarnessRequest) -> dict[str, Any]:
        messages: list[dict[str, str]] = []
        if request.system_prompt:
            messages.append({"role": "system", "content": request.system_prompt})
        messages.append({"role": "user", "content": self._prepare_prompt(request)})
        payload: dict[str, Any] = {
            "model": request.config.model_name,
            "messages": messages,
            "temperature": request.temperature,
            "max_tokens": request.max_tokens,
        }
        reasoning_effort = request.config.reasoning_effort
        if reasoning_effort is None and self._is_qwen_model(request.config.model_name):
            # LM Studio's OpenAI-compatible endpoint honors this for Qwen
            # thinking models. Prompt-level /no_think is not reliable there.
            reasoning_effort = "none"
        if reasoning_effort is not None:
            payload["reasoning_effort"] = reasoning_effort
        if request.metadata:
            payload["metadata"] = request.metadata
        return payload

    def _prepare_prompt(self, request: LLMHarnessRequest) -> str:
        """Apply model-specific prompt controls at the harness boundary."""
        return request.prompt

    def _is_qwen_model(self, model_name: str) -> bool:
        """Return whether the target model is a Qwen-family model."""
        return "qwen" in model_name.lower()

    def _post_json(self, config: LLMHTTPConfig, path: str, payload: dict[str, Any]) -> dict[str, Any]:
        url = config.base_url.rstrip("/") + path
        headers = {"Content-Type": "application/json"}
        if config.api_key:
            headers["Authorization"] = f"Bearer {config.api_key}"
        http_request = urllib.request.Request(
            url=url,
            data=json.dumps(payload).encode("utf-8"),
            headers=headers,
            method="POST",
        )
        try:
            with urllib.request.urlopen(http_request, timeout=config.timeout_seconds) as response:
                return json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as error:
            body = error.read().decode("utf-8", errors="replace")
            raise RuntimeError(f"LLM HTTP {error.code}: {body}") from error

    def _extract_content(self, response_payload: dict[str, Any]) -> str:
        choices = response_payload.get("choices") or []
        if not choices:
            return ""
        message = choices[0].get("message") or {}
        content = message.get("content") or ""
        if content:
            return str(content)
        return str(message.get("reasoning_content") or "")

    def _write_output_file(self, request: LLMHarnessRequest, content: str) -> Path | None:
        if not request.output_path:
            return None
        workspace = Path(request.working_directory).expanduser().resolve()
        target = (workspace / request.output_path).resolve()
        try:
            target.relative_to(workspace)
        except ValueError as error:
            raise RuntimeError(f"LLM output path escapes workspace: {target}") from error
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
        return target

    def _failed(
        self,
        request: LLMHarnessRequest,
        started: float,
        error: str,
        raw_response: dict[str, Any] | None = None,
    ) -> LLMHarnessResult:
        if request.stream_callback is not None:
            request.stream_callback("stderr", f"[llm-harness] {error}\n")
        return LLMHarnessResult(
            success=False,
            content="",
            duration_ms=self._duration_ms(started),
            model_name=request.config.model_name,
            error=error,
            raw_response=raw_response,
        )

    def _duration_ms(self, started: float) -> int:
        return int((perf_counter() - started) * 1000)
