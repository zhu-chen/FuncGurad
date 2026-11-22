"""工具注册表：对 Agent 暴露工具 schema，并统一封装调用结果与计时。"""

from __future__ import annotations

import time
from typing import Any, Callable

from repo_tools import find_files, grep_content, read_file
from repo_tools.base import SnapshotGuard, ToolError, ToolResult

Handler = Callable[[SnapshotGuard, dict], dict]

_HANDLERS: dict[str, Handler] = {
    "grep_content": grep_content.run,
    "find_files": find_files.run,
    "read_file": read_file.run,
}

TOOL_SCHEMAS: list[dict[str, Any]] = [
    {
        "type": "function",
        "function": {
            "name": "grep_content",
            "description": (
                "在只读仓库快照中按模式逐行搜索文件内容，返回文件相对路径、行号与命中行。"
                "适合确认函数实现、可疑的内存/长度处理。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "pattern": {
                        "type": "string",
                        "description": "正则表达式；fixed_string=true 时按字面量匹配",
                    },
                    "scope": {
                        "type": "string",
                        "description": "可选，限定在某个快照内相对文件或目录，例如 coders/psd.c",
                    },
                    "path_glob": {
                        "type": "string",
                        "description": "可选，按文件名/相对路径过滤，例如 *.c",
                    },
                    "ignore_case": {
                        "type": "boolean",
                        "description": "是否忽略大小写，默认 false",
                    },
                    "fixed_string": {
                        "type": "boolean",
                        "description": "是否按固定字符串而不是正则匹配，默认 false",
                    },
                    "max_results": {
                        "type": "integer",
                        "minimum": 1,
                        "maximum": 200,
                        "description": "最多返回的命中行数，默认 100",
                    },
                },
                "required": ["pattern"],
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "find_files",
            "description": (
                "在只读仓库快照中按文件名查找文件，支持通配符（如 *.c、*psd*）。"
                "不含通配符时按子串匹配，返回快照内相对路径。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "pattern": {
                        "type": "string",
                        "description": "文件名模式或子串，例如 *.c、*psd*",
                    },
                    "path_prefix": {
                        "type": "string",
                        "description": "可选，限定在某个快照内相对目录",
                    },
                    "max_results": {
                        "type": "integer",
                        "minimum": 1,
                        "maximum": 200,
                        "description": "最多返回的文件数，默认 50",
                    },
                },
                "required": ["pattern"],
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "read_file",
            "description": (
                "读取快照内指定文件的一段内容，返回带行号的文本。"
                "先用 grep_content 或 find_files 定位，再读取附近片段。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "快照内相对文件路径",
                    },
                    "start_line": {
                        "type": "integer",
                        "minimum": 1,
                        "description": "起始行号（从 1 开始），默认 1",
                    },
                    "end_line": {
                        "type": "integer",
                        "minimum": 1,
                        "description": "结束行号（含）；不填则按 max_lines 计算",
                    },
                    "max_lines": {
                        "type": "integer",
                        "minimum": 1,
                        "maximum": 400,
                        "description": "未指定 end_line 时最多读取的行数，默认 200",
                    },
                },
                "required": ["path"],
                "additionalProperties": False,
            },
        },
    },
]


def tool_names() -> list[str]:
    return list(_HANDLERS)


def dispatch(guard: SnapshotGuard, name: str, arguments: dict[str, Any] | None) -> ToolResult:
    """执行一次工具调用，任何异常都转成 ok=false，不中断 Agent。"""
    arguments = arguments or {}
    handler = _HANDLERS.get(name)
    if handler is None:
        return ToolResult(
            ok=False,
            tool=name,
            arguments=arguments,
            error=f"未知工具: {name}；可用工具: {', '.join(_HANDLERS)}",
        )

    started = time.perf_counter()
    try:
        data = handler(guard, arguments)
        return ToolResult(
            ok=True,
            tool=name,
            arguments=arguments,
            data=data,
            elapsed_ms=(time.perf_counter() - started) * 1000,
        )
    except ToolError as exc:
        return ToolResult(
            ok=False,
            tool=name,
            arguments=arguments,
            error=str(exc),
            elapsed_ms=(time.perf_counter() - started) * 1000,
        )
    except Exception as exc:  # 兜底：工具实现错误不能拖垮整个 run
        return ToolResult(
            ok=False,
            tool=name,
            arguments=arguments,
            error=f"{type(exc).__name__}: {exc}",
            elapsed_ms=(time.perf_counter() - started) * 1000,
        )


def summarize(result: ToolResult) -> dict[str, Any]:
    """生成适合写入 TraceEvent 与报告的精简摘要。"""
    summary: dict[str, Any] = {"ok": result.ok}
    data = result.data or {}

    if result.tool == "find_files" and result.ok:
        summary["total_matches"] = data.get("total_matches", 0)
        summary["paths"] = [item["path"] for item in data.get("matches", [])[:3]]
        summary["scanned_files"] = data.get("scanned_files", 0)
    elif result.tool == "grep_content" and result.ok:
        summary["total_matches"] = data.get("total_matches", 0)
        summary["hits"] = [
            {"path": item["path"], "line": item["line"]}
            for item in data.get("matches", [])[:3]
        ]
        summary["scanned_files"] = data.get("scanned_files", 0)
    elif result.tool == "read_file" and result.ok:
        summary["path"] = data.get("path")
        summary["start_line"] = data.get("start_line")
        summary["end_line"] = data.get("end_line")
        summary["total_lines"] = data.get("total_lines")

    if result.error:
        summary["error"] = result.error
    summary["truncated"] = bool(data.get("truncated", False))
    return summary
