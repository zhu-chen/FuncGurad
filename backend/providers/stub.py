"""确定性 stub provider：不联网、不做语义漏洞判断，只验证端到端链路。

它根据对话中已有的工具结果决定下一步，模拟一个最简单的自主循环：
find_files -> grep_content -> read_file -> 收尾。
"""

from __future__ import annotations

import json
import re
from typing import Any

from backend.providers.base import AssistantTurn, Provider, ToolCall

KEYWORDS = {
    "if",
    "for",
    "while",
    "switch",
    "return",
    "sizeof",
    "static",
    "const",
    "unsigned",
    "signed",
    "typedef",
}


def _extract_user_content(messages: list[dict[str, Any]]) -> str:
    for message in messages:
        if message.get("role") == "user":
            return str(message.get("content") or "")
    return ""


def _extract_function_name(text: str) -> str:
    fenced = re.search(r"```[a-zA-Z0-9]*\n(.*?)```", text, re.S)
    code = fenced.group(1) if fenced else text
    for match in re.finditer(r"\b([A-Za-z_][A-Za-z0-9_]{2,})\s*\(", code):
        name = match.group(1)
        if name.lower() not in KEYWORDS:
            return name
    return ""


def _file_pattern(function_name: str) -> str:
    """用函数名中的大写缩写猜源文件名，例如 ReadPSDChannelPixels -> *psd*.c。"""
    tokens = [
        token
        for token in re.findall(
            r"[A-Z]+(?=[A-Z][a-z])|[A-Z][a-z0-9]*|[A-Z]+", function_name
        )
        if len(token) >= 2 and token.isupper()
    ]
    if tokens:
        return f"*{tokens[0].lower()}*.c"
    return "*.c"


def _last_tool_data(messages: list[dict[str, Any]], tool: str) -> dict[str, Any]:
    for message in reversed(messages):
        if message.get("role") != "tool":
            continue
        try:
            payload = json.loads(message.get("content") or "{}")
        except json.JSONDecodeError:
            continue
        if payload.get("tool") == tool and payload.get("ok"):
            return payload.get("data") or {}
    return {}


def _tool_steps(messages: list[dict[str, Any]]) -> int:
    return sum(
        1
        for message in messages
        if message.get("role") == "assistant" and message.get("tool_calls")
    )


class StubProvider(Provider):
    name = "stub"
    is_stub = True

    def complete(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
        timeout_seconds: int,
    ) -> AssistantTurn:
        user_content = _extract_user_content(messages)
        function_name = _extract_function_name(user_content)
        steps = _tool_steps(messages)

        if steps == 0:
            pattern = _file_pattern(function_name)
            return AssistantTurn(
                content=f"先按函数名线索查找相关源文件（pattern={pattern}）。",
                tool_calls=[
                    ToolCall(
                        id="stub-step1-find",
                        name="find_files",
                        arguments={"pattern": pattern, "max_results": 20},
                    )
                ],
            )

        if steps == 1:
            found = _last_tool_data(messages, "find_files").get("matches") or []
            scope = found[0]["path"] if found else ""
            pattern = function_name or "."
            arguments: dict[str, Any] = {
                "pattern": re.escape(pattern) if function_name else pattern,
                "fixed_string": bool(function_name),
                "max_results": 20,
            }
            if scope:
                arguments["scope"] = scope
            else:
                arguments["path_glob"] = "*.c"
            return AssistantTurn(
                content="在候选文件中搜索待分析函数的定义位置。",
                tool_calls=[
                    ToolCall(
                        id="stub-step2-grep",
                        name="grep_content",
                        arguments=arguments,
                    )
                ],
            )

        if steps == 2:
            hits = _last_tool_data(messages, "grep_content").get("matches") or []
            found = _last_tool_data(messages, "find_files").get("matches") or []
            if hits:
                hit = hits[0]
                start = max(1, int(hit["line"]) - 40)
                end = int(hit["line"]) + 160
                arguments = {
                    "path": hit["path"],
                    "start_line": start,
                    "end_line": end,
                }
            elif found:
                arguments = {"path": found[0]["path"], "max_lines": 200}
            else:
                return self._final_turn(
                    "stub provider 未能在快照中定位到目标函数，缺少可读证据。"
                )
            return AssistantTurn(
                content="读取命中位置附近的实现，确认上下文。",
                tool_calls=[
                    ToolCall(
                        id="stub-step3-read",
                        name="read_file",
                        arguments=arguments,
                    )
                ],
            )

        return self._final_turn(
            "stub provider 只负责验证端到端链路：已完成文件查找、内容搜索与片段读取，"
            "但未做真实语义漏洞判断。现有工具结果不足以证明漏洞，按证据门控给出 benign。"
        )

    @staticmethod
    def _final_turn(reason: str) -> AssistantTurn:
        answer = json.dumps(
            {"verdict": "benign", "reason": reason, "evidence": []},
            ensure_ascii=False,
        )
        return AssistantTurn(content=answer, tool_calls=[])
