"""OpenAI 兼容 Chat Completions provider（支持 function calling）。

适用于智谱 GLM（https://open.bigmodel.cn/api/paas/v4）等兼容接口。
base_url、key、model 全部来自环境变量或本地 .env.local；key 不写入源码。

GLM-5.3-Flash 强制开启 thinking，长推理容易超过非流式请求的超时；
这里默认使用 SSE 流式响应，并把 reasoning_content 与工具调用一起带回循环。
"""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
from typing import Any

from backend.providers.base import AssistantTurn, Provider, ProviderError, ToolCall


def _parse_arguments(raw: Any) -> dict[str, Any]:
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, str):
        try:
            value = json.loads(raw)
        except json.JSONDecodeError:
            return {"_raw": raw}
        return value if isinstance(value, dict) else {}
    return {}


def _content_text(value: Any) -> str:
    if isinstance(value, list):
        return "".join(
            str(part.get("text") or "")
            for part in value
            if isinstance(part, dict)
        )
    return str(value or "")


def _turn_from_message(message: dict[str, Any], payload: Any) -> AssistantTurn:
    tool_calls: list[ToolCall] = []
    for index, call in enumerate(message.get("tool_calls") or []):
        function = call.get("function") or {}
        tool_calls.append(
            ToolCall(
                id=call.get("id") or f"call-{index}",
                name=function.get("name") or "",
                arguments=_parse_arguments(function.get("arguments")),
            )
        )
    return AssistantTurn(
        content=_content_text(message.get("content")),
        tool_calls=tool_calls,
        reasoning_content=str(message.get("reasoning_content") or ""),
        raw=payload if isinstance(payload, dict) else None,
    )


class OpenAICompatibleProvider(Provider):
    name = "openai-compatible"

    def __init__(
        self,
        base_url: str,
        api_key: str,
        model: str,
        temperature: float,
        proxy: str,
        max_tokens: int = 16384,
        stream: bool = True,
        reasoning_effort: str = "low",
        retries: int = 2,
    ) -> None:
        if not api_key:
            raise ProviderError("缺少 API key，无法使用 OpenAI 兼容 provider")
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.model = model
        self.temperature = temperature
        self.proxy = proxy
        self.max_tokens = max_tokens
        self.stream = stream
        self.reasoning_effort = reasoning_effort
        self.retries = max(0, retries)

    def _request_body(
        self, messages: list[dict[str, Any]], tools: list[dict[str, Any]]
    ) -> dict[str, Any]:
        body: dict[str, Any] = {
            "model": self.model,
            "messages": messages,
            "temperature": self.temperature,
            "stream": self.stream,
        }
        if tools:
            body["tools"] = tools
            body["tool_choice"] = "auto"
        if self.max_tokens > 0:
            body["max_tokens"] = self.max_tokens
        if "bigmodel.cn" in self.base_url:
            # GLM-5.3-Flash 强制开启 thinking；保留 reasoning 以便多轮工具调用。
            body["thinking"] = {"type": "enabled", "clear_thinking": False}
            if self.reasoning_effort in ("low", "high", "max"):
                body["reasoning_effort"] = self.reasoning_effort
            if self.stream:
                body["tool_stream"] = True
        return body

    def _opener(self) -> urllib.request.OpenerDirector:
        handlers = []
        if self.proxy:
            handlers.append(
                urllib.request.ProxyHandler(
                    {"http": self.proxy, "https": self.proxy}
                )
            )
        return urllib.request.build_opener(*handlers)

    def complete(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
        timeout_seconds: int,
    ) -> AssistantTurn:
        body = self._request_body(messages, tools)
        data = json.dumps(body, ensure_ascii=False).encode("utf-8")
        headers = {
            "Content-Type": "application/json",
            "Accept": "text/event-stream" if self.stream else "application/json",
            "Authorization": f"Bearer {self.api_key}",
        }
        opener = self._opener()
        last_error: ProviderError | None = None
        for attempt in range(self.retries + 1):
            request = urllib.request.Request(
                f"{self.base_url}/chat/completions",
                data=data,
                headers=headers,
                method="POST",
            )
            try:
                if self.stream:
                    return self._complete_stream(
                        opener, request, timeout_seconds
                    )
                return self._complete_once(opener, request, timeout_seconds)
            except ProviderError as exc:
                last_error = exc
                text = str(exc)
                fatal = any(
                    marker in text
                    for marker in ("HTTP 400", "HTTP 401", "HTTP 403", "HTTP 404")
                )
                if fatal or attempt >= self.retries:
                    raise
                time.sleep(min(10, 2**attempt))
        raise last_error or ProviderError("LLM 请求失败")

    def _complete_once(
        self,
        opener: urllib.request.OpenerDirector,
        request: urllib.request.Request,
        timeout_seconds: int,
    ) -> AssistantTurn:
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

        if isinstance(payload, dict) and payload.get("error"):
            raise ProviderError(f"LLM 接口错误: {str(payload['error'])[:500]}")
        try:
            message = payload["choices"][0]["message"]
        except (KeyError, IndexError, TypeError) as exc:
            raise ProviderError(f"LLM 响应结构异常: {str(payload)[:300]}") from exc
        return _turn_from_message(message, payload)

    def _complete_stream(
        self,
        opener: urllib.request.OpenerDirector,
        request: urllib.request.Request,
        timeout_seconds: int,
    ) -> AssistantTurn:
        deadline = time.monotonic() + max(1, timeout_seconds)
        content_parts: list[str] = []
        reasoning_parts: list[str] = []
        calls: dict[int, dict[str, str]] = {}
        usage: Any = None
        finish_reason: str | None = None
        saw_data = False

        try:
            with opener.open(request, timeout=max(1, timeout_seconds)) as response:
                for raw in response:
                    if time.monotonic() > deadline:
                        raise ProviderError("LLM 流式响应超时")
                    line = raw.decode("utf-8", errors="ignore").strip()
                    if not line or line.startswith(":") or line.startswith("event:"):
                        continue
                    if not line.startswith("data:"):
                        continue
                    data_text = line[len("data:") :].strip()
                    if data_text == "[DONE]":
                        break
                    try:
                        chunk = json.loads(data_text)
                    except json.JSONDecodeError:
                        continue
                    if not isinstance(chunk, dict):
                        continue
                    saw_data = True
                    if chunk.get("error"):
                        raise ProviderError(
                            f"LLM 流式错误: {str(chunk['error'])[:500]}"
                        )
                    if chunk.get("usage"):
                        usage = chunk["usage"]
                    choices = chunk.get("choices") or []
                    if not choices:
                        continue
                    choice = choices[0] or {}
                    finish_reason = choice.get("finish_reason") or finish_reason
                    delta = choice.get("delta") or {}
                    if delta.get("content"):
                        content_parts.append(str(delta["content"]))
                    if delta.get("reasoning_content"):
                        reasoning_parts.append(str(delta["reasoning_content"]))
                    for item in delta.get("tool_calls") or []:
                        index = item.get("index")
                        if not isinstance(index, int):
                            index = 0
                        entry = calls.setdefault(
                            index, {"id": "", "name": "", "arguments": ""}
                        )
                        if item.get("id"):
                            entry["id"] = str(item["id"])
                        function = item.get("function") or {}
                        if function.get("name"):
                            entry["name"] = str(function["name"])
                        raw_arguments = function.get("arguments")
                        if isinstance(raw_arguments, str):
                            entry["arguments"] += raw_arguments
                        elif isinstance(raw_arguments, dict):
                            entry["arguments"] = json.dumps(
                                raw_arguments, ensure_ascii=False
                            )
                        elif raw_arguments:
                            entry["arguments"] += str(raw_arguments)
        except ProviderError:
            raise
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="ignore")[:500]
            raise ProviderError(
                f"LLM 接口返回 HTTP {exc.code}: {detail}"
            ) from exc
        except Exception as exc:
            raise ProviderError(f"LLM 请求失败: {exc}") from exc

        if not saw_data and not content_parts and not reasoning_parts and not calls:
            raise ProviderError("LLM 流式响应为空")

        tool_calls = [
            ToolCall(
                id=calls[index]["id"] or f"call-{index}",
                name=calls[index]["name"],
                arguments=_parse_arguments(calls[index]["arguments"]),
            )
            for index in sorted(calls)
        ]
        return AssistantTurn(
            content="".join(content_parts),
            tool_calls=tool_calls,
            reasoning_content="".join(reasoning_parts),
            raw={
                "streamed": True,
                "finish_reason": finish_reason,
                "usage": usage,
            },
        )