"""本地环境变量加载：读取仓库根目录的 .env.local / .env。

真实进程环境变量优先；文件只补充尚未设置的变量，且不会打印任何值。
这样可以统一保存模型 key、代理等本机配置，而无需写入源码或 README。
"""

from __future__ import annotations

import os
import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
ENV_FILES = (REPO_ROOT / ".env.local", REPO_ROOT / ".env")
KEY_PATTERN = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def _parse_line(line: str) -> tuple[str, str] | None:
    """解析单行 KEY=VALUE；支持空行、# 注释、export 前缀与成对引号。"""
    text = line.strip()
    if not text or text.startswith("#"):
        return None
    if text.startswith("export "):
        text = text[len("export ") :].lstrip()
    if "=" not in text:
        return None
    key, value = text.split("=", 1)
    key = key.strip()
    if not KEY_PATTERN.match(key):
        return None
    value = value.strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in ("'", '"'):
        value = value[1:-1]
    return key, value


def load_env_file(paths: tuple[Path, ...] | None = None) -> list[Path]:
    """加载 .env.local 与 .env；返回实际读取的文件列表。"""
    loaded: list[Path] = []
    for path in paths or ENV_FILES:
        if not path.is_file():
            continue
        try:
            text = path.read_text(encoding="utf-8-sig")
        except OSError:
            continue
        for line in text.splitlines():
            parsed = _parse_line(line)
            if parsed is None:
                continue
            key, value = parsed
            # 真实环境变量优先，文件只补缺。
            if key not in os.environ:
                os.environ[key] = value
        loaded.append(path)
    return loaded