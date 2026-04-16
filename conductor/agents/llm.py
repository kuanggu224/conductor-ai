"""LLMBackend 抽象与最小 mock 实现。"""

from __future__ import annotations

import json
import urllib.request
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import StrEnum

from conductor.context.models import ContextPack


class LLMProvider(StrEnum):
    """LLM backend 类型。"""

    LOCAL = "local"
    CLOUD = "cloud"
    HYBRID = "hybrid"


@dataclass(slots=True)
class LLMRequest:
    """统一的 LLM 请求结构。"""

    user_prompt: str
    system_prompt: str = ""
    context_pack: ContextPack | None = None
    preferred_backend: str | None = None
    metadata: dict[str, str] = field(default_factory=dict)


@dataclass(slots=True)
class LLMResponse:
    """统一的 LLM 响应结构。"""

    content: str
    provider: LLMProvider
    backend_name: str
    model_name: str


@dataclass(slots=True)
class LLMHTTPConfig:
    """HTTP 类 LLM backend 配置。"""

    base_url: str
    model_name: str
    api_key: str | None = None
    timeout_seconds: float = 30.0
    enabled: bool = False


class LLMBackend(ABC):
    """本地模型 / 云端模型统一接口。"""

    provider: LLMProvider
    backend_name: str
    model_name: str

    @abstractmethod
    def generate(self, request: LLMRequest) -> LLMResponse:
        """执行一次生成。"""


@dataclass(slots=True)
class MockLocalLLMBackend(LLMBackend):
    """本地模型 mock backend。"""

    backend_name: str = "mock_local"
    model_name: str = "local-demo-model"
    provider: LLMProvider = LLMProvider.LOCAL

    def generate(self, request: LLMRequest) -> LLMResponse:
        """返回本地模型的 mock 响应。"""
        context_hint = self._build_context_hint(request.context_pack)
        return LLMResponse(
            content=f"[local] 基于本地模型处理: {request.user_prompt}{context_hint}",
            provider=self.provider,
            backend_name=self.backend_name,
            model_name=self.model_name,
        )

    def _build_context_hint(self, context_pack: ContextPack | None) -> str:
        """从 ContextPack 中提取最小提示。"""
        if context_pack and context_pack.current_workitem:
            return f" | workitem={context_pack.current_workitem.id}"
        return ""


@dataclass(slots=True)
class MockCloudLLMBackend(LLMBackend):
    """云端模型 mock backend。"""

    backend_name: str = "mock_cloud"
    model_name: str = "cloud-demo-model"
    provider: LLMProvider = LLMProvider.CLOUD

    def generate(self, request: LLMRequest) -> LLMResponse:
        """返回云端模型的 mock 响应。"""
        quality_tier = request.metadata.get("quality_tier", "standard")
        return LLMResponse(
            content=f"[cloud:{quality_tier}] 基于云端模型处理: {request.user_prompt}",
            provider=self.provider,
            backend_name=self.backend_name,
            model_name=self.model_name,
        )


@dataclass(slots=True)
class HybridLLMBackend(LLMBackend):
    """根据请求策略在本地与云端之间路由。"""

    local_backend: LLMBackend
    cloud_backend: LLMBackend
    backend_name: str = "hybrid_router"
    model_name: str = "hybrid"
    provider: LLMProvider = LLMProvider.HYBRID

    def generate(self, request: LLMRequest) -> LLMResponse:
        """按规则选择 local/cloud backend。"""
        selected = self._select_backend(request)
        return selected.generate(request)

    def _select_backend(self, request: LLMRequest) -> LLMBackend:
        """选择实际 backend。"""
        if request.preferred_backend == LLMProvider.LOCAL.value:
            return self.local_backend
        if request.preferred_backend == LLMProvider.CLOUD.value:
            return self.cloud_backend
        if request.metadata.get("quality_tier") == "high":
            return self.cloud_backend
        if request.metadata.get("latency_tier") == "low":
            return self.local_backend
        return self.local_backend


@dataclass(slots=True)
class OpenAICompatibleCloudLLMBackend(LLMBackend):
    """面向云端 OpenAI-compatible API 的 backend 壳。"""

    config: LLMHTTPConfig
    backend_name: str = "openai_compatible_cloud"
    provider: LLMProvider = LLMProvider.CLOUD

    @property
    def model_name(self) -> str:
        """返回配置中的模型名。"""
        return self.config.model_name

    def generate(self, request: LLMRequest) -> LLMResponse:
        """调用 OpenAI SDK 的 OpenAI-compatible 接口或返回未启用提示。"""
        if not self.config.enabled:
            return LLMResponse(
                content=f"[cloud-disabled] 未启用云端 LLM backend: {request.user_prompt}",
                provider=self.provider,
                backend_name=self.backend_name,
                model_name=self.model_name,
            )
        client = self._build_client()
        response = client.chat.completions.create(
            model=self.model_name,
            messages=self._build_chat_messages(request),
        )
        content = response.choices[0].message.content or ""
        return LLMResponse(
            content=content,
            provider=self.provider,
            backend_name=self.backend_name,
            model_name=self.model_name,
        )

    def _build_chat_messages(self, request: LLMRequest) -> list[dict[str, str]]:
        """构建 OpenAI-compatible chat messages。"""
        messages: list[dict[str, str]] = []
        if request.system_prompt:
            messages.append({"role": "system", "content": request.system_prompt})
        messages.append({"role": "user", "content": self._merge_prompt_with_context(request)})
        return messages

    def _merge_prompt_with_context(self, request: LLMRequest) -> str:
        """把最小上下文拼接进 prompt。"""
        context_text = build_context_text(request.context_pack)
        if context_text:
            return f"{request.user_prompt}\n\n# 可用上下文\n{context_text}"
        return request.user_prompt

    def _build_client(self):
        """按需构建 OpenAI SDK client。"""
        try:
            from openai import OpenAI
        except ImportError as error:
            raise RuntimeError("未安装 openai SDK，请先安装 requirements.txt 中的依赖") from error
        if not self.config.api_key:
            raise RuntimeError("云端 LLM backend 已启用，但未配置 API key")
        return OpenAI(
            base_url=self.config.base_url,
            api_key=self.config.api_key,
            timeout=self.config.timeout_seconds,
        )


@dataclass(slots=True)
class LocalModelHTTPBackend(LLMBackend):
    """面向本地 OpenAI-compatible 或推理服务的 backend 壳。"""

    config: LLMHTTPConfig
    backend_name: str = "local_model_http"
    provider: LLMProvider = LLMProvider.LOCAL

    @property
    def model_name(self) -> str:
        """返回配置中的模型名。"""
        return self.config.model_name

    def generate(self, request: LLMRequest) -> LLMResponse:
        """调用本地服务或返回未启用提示。"""
        if not self.config.enabled:
            return LLMResponse(
                content=f"[local-disabled] 未启用本地 LLM backend: {request.user_prompt}",
                provider=self.provider,
                backend_name=self.backend_name,
                model_name=self.model_name,
            )
        payload = self._build_generation_payload(request)
        response_payload = self._post_json("/chat/completions", payload)
        content = response_payload["choices"][0]["message"]["content"]
        return LLMResponse(
            content=content,
            provider=self.provider,
            backend_name=self.backend_name,
            model_name=self.model_name,
        )

    def _build_generation_payload(self, request: LLMRequest) -> dict:
        """构建本地服务请求体。"""
        prompt = request.user_prompt
        context_text = build_context_text(request.context_pack)
        if context_text:
            prompt = f"{prompt}\n\n# 可用上下文\n{context_text}"
        return {
            "model": self.model_name,
            "messages": [{"role": "user", "content": prompt}],
            "metadata": request.metadata,
        }

    def _post_json(self, path: str, payload: dict) -> dict:
        """发起 JSON POST 请求。"""
        url = self.config.base_url.rstrip("/") + path
        body = json.dumps(payload).encode("utf-8")
        request = urllib.request.Request(
            url=url,
            data=body,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(request, timeout=self.config.timeout_seconds) as response:
            return json.loads(response.read().decode("utf-8"))


def build_context_text(context_pack: ContextPack | None) -> str:
    """把 ContextPack 格式化为可注入 LLM 的文本。"""
    if context_pack is None:
        return ""
    sections: list[str] = []
    if context_pack.relevant_state is not None:
        state = context_pack.relevant_state
        sections.append(
            "## 项目状态\n"
            f"- 项目目标: {state.project.goal}\n"
            f"- 当前阶段: {state.current_stage or '-'}\n"
            f"- 项目状态: {state.project_status.value}"
        )
    if context_pack.current_workitem is not None:
        workitem = context_pack.current_workitem
        sections.append(
            "## 当前工作项\n"
            f"- ID: {workitem.id}\n"
            f"- 类型: {workitem.kind}\n"
            f"- 阶段: {workitem.stage}\n"
            f"- 描述: {workitem.description}"
        )
    if context_pack.acceptance_criteria:
        criteria = "\n".join(f"- {item}" for item in context_pack.acceptance_criteria)
        sections.append(f"## 验收标准\n{criteria}")
    if context_pack.artifacts:
        artifact_text = "\n\n".join(context_pack.artifacts)
        sections.append(f"## 相关历史产物\n{artifact_text}")
    if context_pack.relevant_memory and context_pack.relevant_memory.decision_memory:
        decisions = "\n".join(f"- {item}" for item in context_pack.relevant_memory.decision_memory)
        sections.append(f"## 最近 Gate 记录\n{decisions}")
    return "\n\n".join(sections)
