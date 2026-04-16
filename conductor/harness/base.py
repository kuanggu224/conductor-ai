"""Harness 基类。"""

from __future__ import annotations

from abc import ABC, abstractmethod

from conductor.harness.models import HarnessRequest, HarnessResult


class BaseHarness(ABC):
    """统一 Harness 协议。"""

    name: str = "base"

    @abstractmethod
    def run(self, request: HarnessRequest) -> HarnessResult:
        """执行一次受控请求。"""
        raise NotImplementedError
