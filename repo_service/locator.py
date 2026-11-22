"""函数定位 v1：在只读快照中按规范化文本查找目标函数。

测试集没有 file_name，因此先用函数文本前 240 个规范化字符做探针，
命中后再用完整规范化函数确认；匹配成功返回相对路径与置信度。
"""

from __future__ import annotations

import os
import re
import time
from dataclasses import dataclass, field
from pathlib import Path

SOURCE_EXTENSIONS = {
    ".c",
    ".h",
    ".cc",
    ".cpp",
    ".cxx",
    ".hpp",
    ".hh",
    ".hxx",
    ".inl",
    ".inc",
    ".m",
    ".mm",
}

SKIP_DIRS = {".git", ".svn", ".hg"}

WHITESPACE = re.compile(r"\s+")
PROBE_LENGTH = 240


@dataclass
class LocateResult:
    status: str
    path: str | None = None
    confidence: float = 0.0
    method: str = "normalized-substring"
    candidates: list[str] = field(default_factory=list)
    scanned_files: int = 0
    elapsed_seconds: float = 0.0


def normalize_whitespace(text: str) -> str:
    return WHITESPACE.sub(" ", text).strip()


def locate_function(
    snapshot_dir: Path,
    func_text: str,
    max_file_bytes: int,
    max_results: int = 5,
) -> LocateResult:
    started = time.time()
    target = normalize_whitespace(func_text)
    if not target:
        return LocateResult(
            status="not_found",
            elapsed_seconds=round(time.time() - started, 2),
        )

    probe = target[:PROBE_LENGTH]
    found: list[str] = []
    partial: list[str] = []
    scanned = 0

    for root, dir_names, file_names in os.walk(snapshot_dir):
        dir_names[:] = sorted(
            name for name in dir_names if name not in SKIP_DIRS
        )
        for file_name in sorted(file_names):
            suffix = Path(file_name).suffix.lower()
            if suffix not in SOURCE_EXTENSIONS:
                continue

            path = Path(root) / file_name
            try:
                if path.stat().st_size > max_file_bytes:
                    continue
                raw_bytes = path.read_bytes()
            except OSError:
                continue

            if b"\x00" in raw_bytes[:8192]:
                continue

            scanned += 1
            normalized = normalize_whitespace(
                raw_bytes.decode("utf-8", errors="ignore")
            )
            if not normalized or probe not in normalized:
                continue

            relative = path.relative_to(snapshot_dir).as_posix()
            if target in normalized:
                found.append(relative)
                if len(found) >= max_results:
                    break
            elif len(partial) < max_results:
                partial.append(relative)

        if len(found) >= max_results:
            break

    elapsed = round(time.time() - started, 2)
    if found:
        return LocateResult(
            status="found",
            path=found[0],
            confidence=1.0,
            candidates=found,
            scanned_files=scanned,
            elapsed_seconds=elapsed,
        )
    if partial:
        return LocateResult(
            status="partial",
            path=partial[0],
            confidence=0.5,
            candidates=partial,
            scanned_files=scanned,
            elapsed_seconds=elapsed,
        )
    return LocateResult(
        status="not_found",
        scanned_files=scanned,
        elapsed_seconds=elapsed,
    )
