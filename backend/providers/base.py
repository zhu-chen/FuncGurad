"""模型 provider 的公共协议。"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


class ProviderError(RuntimeError):
    """模型调用失败；由 Agent 循环捕获并记录到 TraceEvent。"""


@dataclass
class ToolCall:
    id: str
    name: str
    arguments: dict[str, Any]


@dataclass
class AssistantTurn:
    content: str = ""
    tool_calls: list[ToolCall] = field(default_factory=list)
    raw: dict[str, Any] | None = None


class Provider:
    """最小 provider 接口：输入对话与工具 schema，输出一步助手消息。"""

    name = "provider"
    is_stub = False

    def complete(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
        timeout_seconds: int,
    ) -> AssistantTurn:
        raise NotImplementedError
