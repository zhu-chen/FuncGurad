"""按文件名查找：支持通配符与子串匹配，只返回快照内相对路径。"""

from __future__ import annotations

import fnmatch
from pathlib import Path

from repo_tools.base import SnapshotGuard, ToolError


def run(guard: SnapshotGuard, arguments: dict) -> dict:
    pattern = str(arguments.get("pattern") or "").strip()
    if not pattern:
        raise ToolError("pattern 不能为空")
    if ".." in pattern:
        raise ToolError("pattern 不能包含 ..")

    max_results = int(arguments.get("max_results") or 50)
    if max_results < 1:
        max_results = 1
    max_results = min(max_results, guard.limits.max_results)

    prefix = str(arguments.get("path_prefix") or "").strip()
    base: Path = guard.resolve(prefix) if prefix else guard.root
    if not base.exists():
        raise ToolError(f"path_prefix 不存在: {prefix}")

    has_wildcard = any(char in pattern for char in "*?[")
    matches: list[dict] = []
    scanned = 0
    truncated_scan = False

    for path in guard.iter_files(base):
        if not path.is_file():
            continue
        if scanned >= guard.limits.max_scan_files:
            truncated_scan = True
            break
        scanned += 1

        name = path.name
        if has_wildcard:
            hit = fnmatch.fnmatch(name, pattern)
        else:
            hit = pattern.lower() in name.lower()
        if not hit:
            continue

        try:
            size = path.stat().st_size
        except OSError:
            size = 0
        matches.append({"path": guard.relative(path), "size_bytes": size})
        if len(matches) >= max_results:
            break

    return {
        "matches": matches,
        "total_matches": len(matches),
        "scanned_files": scanned,
        "truncated": len(matches) >= max_results or truncated_scan,
    }
