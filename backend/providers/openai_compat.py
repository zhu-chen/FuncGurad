"""OpenAI 兼容 Chat Completions provider（支持 function calling）。

base_url、key、model 全部来自环境变量；请求经本机代理发出，key 不落盘。
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from typing import Any

from backend.providers.base import AssistantTurn, Provider, ProviderError, ToolCall


class OpenAICompatibleProvider(Provider):
    name = "openai-compatible"

    def __init__(
        self,
        base_url: str,
        api_key: str,
        model: str,
        temperature: float,
        proxy: str,
    ) -> None:
        if not api_key:
            raise ProviderError("缺少 API key，无法使用 OpenAI 兼容 provider")
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.model = model
        self.temperature = temperature
        self.proxy = proxy

    def complete(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
        timeout_seconds: int,
    ) -> AssistantTurn:
        body = {
            "model": self.model,
            "messages": messages,
            "tools": tools,
            "tool_choice": "auto",
            "temperature": self.temperature,
        }
        data = json.dumps(body, ensure_ascii=False).encode("utf-8")
        request = urllib.request.Request(
            f"{self.base_url}/chat/completions",
            data=data,
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {self.api_key}",
            },
            method="POST",
        )

        handlers = []
        if self.proxy:
            handlers.append(
                urllib.request.ProxyHandler(
                    {"http": self.proxy, "https": self.proxy}
                )
            )
        opener = urllib.request.build_opener(*handlers)

        try:
            with opener.open(request, timeout=max(1, timeout_seconds)) as response:
                payload = json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="ignore")[:500]
            raise ProviderError(
                f"LLM 接口返回 HTTP {exc.code}: {detail}"
            ) from exc
        except Exception as exc:
            raise ProviderError(f"LLM 请求失败: {exc}") from exc

        try:
            message = payload["choices"][0]["message"]
        except (KeyError, IndexError, TypeError) as exc:
            raise ProviderError(f"LLM 响应结构异常: {str(payload)[:300]}") from exc

        tool_calls: list[ToolCall] = []
        for index, call in enumerate(message.get("tool_calls") or []):
            function = call.get("function") or {}
            raw_arguments = function.get("arguments") or "{}"
            try:
                arguments = json.loads(raw_arguments)
            except json.JSONDecodeError:
                arguments = {"_raw": raw_arguments}
            tool_calls.append(
                ToolCall(
                    id=call.get("id") or f"call-{index}",
                    name=function.get("name") or "",
                    arguments=arguments if isinstance(arguments, dict) else {},
                )
            )

        return AssistantTurn(
            content=message.get("content") or "",
            tool_calls=tool_calls,
            raw=payload,
        )
