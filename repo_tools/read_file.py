"""读取指定文件片段：按行号区间返回，带行号与总行数。"""

from __future__ import annotations

from repo_tools.base import SnapshotGuard, ToolError, truncate_line


def run(guard: SnapshotGuard, arguments: dict) -> dict:
    path_arg = str(arguments.get("path") or "").strip()
    if not path_arg:
        raise ToolError("path 不能为空")

    path = guard.resolve(path_arg)
    if not path.exists():
        raise ToolError(f"文件不存在: {path_arg}")
    if not path.is_file():
        raise ToolError(f"不是文件: {path_arg}")

    text = guard.read_text(path)
    lines = text.splitlines()
    total_lines = len(lines)

    start_line = int(arguments.get("start_line") or 1)
    if start_line < 1:
        start_line = 1

    end_arg = arguments.get("end_line")
    if end_arg is not None:
        end_line = int(end_arg)
    else:
        max_lines = int(arguments.get("max_lines") or guard.limits.max_read_lines)
        max_lines = max(1, min(max_lines, guard.limits.max_read_lines))
        end_line = start_line + max_lines - 1

    end_line = min(end_line, total_lines)
    if end_line < start_line:
        end_line = start_line

    payload = [
        {
            "line": number,
            "text": truncate_line(lines[number - 1], guard.limits.max_line_chars),
        }
        for number in range(start_line, end_line + 1)
    ]

    return {
        "path": guard.relative(path),
        "start_line": start_line,
        "end_line": end_line,
        "total_lines": total_lines,
        "lines": payload,
        "truncated": start_line > 1 or end_line < total_lines,
    }
