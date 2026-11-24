"""底线：只读工具绝不能读到快照目录以外的文件（../、绝对路径、符号链接逃逸）。"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from repo_tools.base import SnapshotGuard, ToolError, ToolLimits
from repo_tools.registry import dispatch


@pytest.fixture()
def snapshot(tmp_path: Path):
    root = tmp_path / "snap"
    (root / "src").mkdir(parents=True)
    (root / "src" / "a.c").write_text("int main(void) {\n  return 0;\n}\n", encoding="utf-8")
    (root / "binary.bin").write_bytes(b"\x00\x01\x02")
    (tmp_path / "secret.txt").write_text("TOP_SECRET\n", encoding="utf-8")
    return root


def test_relative_path_inside_root_allowed(snapshot):
    guard = SnapshotGuard(snapshot)

    resolved = guard.resolve("src/a.c")

    assert resolved == (snapshot / "src" / "a.c").resolve()
    assert guard.relative(resolved) == "src/a.c"


@pytest.mark.parametrize("bad_path", ["../secret.txt", "src/../../secret.txt", "..\\secret.txt"])
def test_parent_escape_rejected(snapshot, bad_path):
    guard = SnapshotGuard(snapshot)

    with pytest.raises(ToolError, match="路径越界"):
        guard.resolve(bad_path)


def test_absolute_path_rejected(snapshot, tmp_path):
    guard = SnapshotGuard(snapshot)

    with pytest.raises(ToolError, match="只接受快照内的相对路径"):
        guard.resolve(str(tmp_path / "secret.txt"))


def test_empty_path_rejected(snapshot):
    guard = SnapshotGuard(snapshot)

    with pytest.raises(ToolError, match="路径不能为空"):
        guard.resolve("   ")


def test_symlink_escape_rejected(snapshot, tmp_path):
    link = snapshot / "escape.txt"
    target = tmp_path / "secret.txt"
    try:
        os.symlink(target, link)
    except (OSError, NotImplementedError):
        pytest.skip("当前环境不允许创建符号链接（需要 Windows 开发者模式或管理员权限）")

    guard = SnapshotGuard(snapshot)

    with pytest.raises(ToolError, match="路径越界"):
        guard.resolve("escape.txt")


def test_binary_file_rejected(snapshot):
    guard = SnapshotGuard(snapshot)

    with pytest.raises(ToolError, match="二进制文件不可读"):
        guard.read_text(snapshot / "binary.bin")


def test_oversized_file_rejected(snapshot):
    guard = SnapshotGuard(snapshot, ToolLimits(max_file_bytes=4))

    with pytest.raises(ToolError, match="超过大小限制"):
        guard.read_text(snapshot / "src" / "a.c")


def test_read_file_tool_rejects_escape(snapshot):
    guard = SnapshotGuard(snapshot)

    result = dispatch(guard, "read_file", {"path": "../secret.txt"})

    assert result.ok is False
    assert "路径越界" in (result.error or "")
    assert result.data == {}


def test_find_files_tool_rejects_escape_prefix(snapshot):
    guard = SnapshotGuard(snapshot)

    result = dispatch(guard, "find_files", {"pattern": "*.txt", "path_prefix": ".."})

    assert result.ok is False
    assert "路径越界" in (result.error or "")


def test_grep_content_tool_rejects_escape_scope(snapshot):
    guard = SnapshotGuard(snapshot)

    result = dispatch(guard, "grep_content", {"pattern": "TOP_SECRET", "scope": "../"})

    assert result.ok is False
    assert "路径越界" in (result.error or "")


def test_find_files_rejects_dotdot_pattern(snapshot):
    guard = SnapshotGuard(snapshot)

    result = dispatch(guard, "find_files", {"pattern": "../*"})

    assert result.ok is False
    assert "pattern 不能包含 .." in (result.error or "")


def test_tools_work_inside_snapshot(snapshot):
    guard = SnapshotGuard(snapshot)

    found = dispatch(guard, "find_files", {"pattern": "*.c"})
    grepped = dispatch(guard, "grep_content", {"pattern": "return 0", "path_glob": "*.c"})
    read = dispatch(guard, "read_file", {"path": "src/a.c", "start_line": 1, "end_line": 2})

    assert found.ok and [item["path"] for item in found.data["matches"]] == ["src/a.c"]
    assert grepped.ok and grepped.data["matches"][0]["line"] == 2
    assert read.ok and read.data["total_lines"] == 3
    assert [line["text"] for line in read.data["lines"]] == ["int main(void) {", "  return 0;"]
