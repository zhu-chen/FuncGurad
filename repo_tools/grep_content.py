"""按模式搜索文件内容：正则或固定字符串，逐行返回命中位置。"""

from __future__ import annotations

import re

from repo_tools.base import (
    BINARY_SNIFF_BYTES,
    SnapshotGuard,
    ToolError,
    matches_glob,
    truncate_line,
)


def run(guard: SnapshotGuard, arguments: dict) -> dict:
    pattern = str(arguments.get("pattern") or "").strip()
    if not pattern:
        raise ToolError("pattern 不能为空")

    ignore_case = bool(arguments.get("ignore_case"))
    fixed_string = bool(arguments.get("fixed_string"))
    max_results = int(arguments.get("max_results") or 100)
    if max_results < 1:
        max_results = 1
    max_results = min(max_results, guard.limits.max_results)

    flags = re.IGNORECASE if ignore_case else 0
    if fixed_string:
        expression = re.compile(re.escape(pattern), flags)
    else:
        try:
            expression = re.compile(pattern, flags)
        except re.error as exc:
            raise ToolError(f"正则表达式无效: {exc}") from exc

    scope = str(arguments.get("scope") or "").strip()
    base = guard.resolve(scope) if scope else guard.root
    if not base.exists():
        raise ToolError(f"scope 不存在: {scope}")

    path_glob = str(arguments.get("path_glob") or "").strip()
    matches: list[dict] = []
    scanned = 0
    truncated_scan = False

    for path in guard.iter_files(base):
        if not path.is_file():
            continue
        relative = guard.relative(path)
        if not matches_glob(relative, path_glob):
            continue
        if scanned >= guard.limits.max_scan_files:
            truncated_scan = True
            break
        scanned += 1

        try:
            if path.stat().st_size > guard.limits.max_file_bytes:
                continue
            raw = path.read_bytes()
        except OSError:
            continue
        if b"\x00" in raw[:BINARY_SNIFF_BYTES]:
            continue

        text = raw.decode("utf-8", errors="ignore")
        for number, line in enumerate(text.splitlines(), 1):
            if not expression.search(line):
                continue
            matches.append(
                {
                    "path": relative,
                    "line": number,
                    "text": truncate_line(line, guard.limits.max_line_chars),
                }
            )
            if len(matches) >= max_results:
                break
        if len(matches) >= max_results:
            break

    return {
        "matches": matches,
        "total_matches": len(matches),
        "scanned_files": scanned,
        "truncated": len(matches) >= max_results or truncated_scan,
    }
