"""只读工具的公共基础设施：路径守卫、统一结果结构与限制常量。"""

from __future__ import annotations

import fnmatch
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterator

SKIP_DIRS = {".git", ".svn", ".hg", ".idea", ".vscode"}
BINARY_SNIFF_BYTES = 8192


class ToolError(Exception):
    """可预期的工具调用失败；消息会原样返回给 Agent。"""


@dataclass(frozen=True)
class ToolLimits:
    """单次工具调用的硬限制，防止大仓库把进程拖死。"""

    max_file_bytes: int = 2_000_000
    max_results: int = 200
    max_read_lines: int = 400
    max_scan_files: int = 6000
    max_line_chars: int = 400


@dataclass
class ToolResult:
    """所有工具统一返回结构。"""

    ok: bool
    tool: str
    arguments: dict[str, Any]
    data: dict[str, Any] = field(default_factory=dict)
    error: str | None = None
    elapsed_ms: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "tool": self.tool,
            "arguments": self.arguments,
            "data": self.data if self.ok else None,
            "error": self.error,
            "elapsed_ms": round(self.elapsed_ms, 2),
        }


class SnapshotGuard:
    """把文件访问严格限制在一个快照目录内。"""

    def __init__(self, root: Path, limits: ToolLimits | None = None) -> None:
        self.root = Path(root).resolve()
        if not self.root.is_dir():
            raise ToolError(f"快照目录不存在: {root}")
        self.limits = limits or ToolLimits()

    def resolve(self, relative: str) -> Path:
        """把快照内相对路径解析为绝对路径；越界或绝对路径直接拒绝。"""
        raw = (relative or "").strip().replace("\\", "/")
        if not raw:
            raise ToolError("路径不能为空")
        candidate_path = Path(raw)
        if candidate_path.is_absolute():
            raise ToolError("只接受快照内的相对路径")
        candidate = (self.root / candidate_path).resolve()
        try:
            candidate.relative_to(self.root)
        except ValueError as exc:
            raise ToolError(f"路径越界，拒绝访问: {relative}") from exc
        return candidate

    def relative(self, path: Path) -> str:
        return path.resolve().relative_to(self.root).as_posix()

    def iter_files(self, base: Path | None = None) -> Iterator[Path]:
        """按稳定顺序遍历文件，跳过版本库目录。"""
        target = base or self.root
        if target.is_file():
            yield target
            return
        for root, dir_names, file_names in os.walk(target):
            dir_names[:] = sorted(name for name in dir_names if name not in SKIP_DIRS)
            for file_name in sorted(file_names):
                yield Path(root) / file_name

    def read_text(self, path: Path) -> str:
        """读取文本文件；超过大小限制或二进制文件直接拒绝。"""
        try:
            if path.stat().st_size > self.limits.max_file_bytes:
                raise ToolError(f"文件超过大小限制: {self.relative(path)}")
            raw = path.read_bytes()
        except OSError as exc:
            raise ToolError(f"读取失败: {exc}") from exc
        if b"\x00" in raw[:BINARY_SNIFF_BYTES]:
            raise ToolError(f"二进制文件不可读: {self.relative(path)}")
        return raw.decode("utf-8", errors="ignore")


def matches_glob(relative_path: str, pattern: str) -> bool:
    """同时匹配相对路径与文件名，便于 `*.c` 这类写法。"""
    if not pattern:
        return True
    name = relative_path.rsplit("/", 1)[-1]
    return fnmatch.fnmatch(relative_path, pattern) or fnmatch.fnmatch(name, pattern)


def truncate_line(text: str, limit: int) -> str:
    text = text.rstrip("\r\n")
    return text if len(text) <= limit else text[:limit] + "..."
