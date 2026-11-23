"""文本规范化与分词：面向中英文漏洞描述与 C/C++ 代码标识符。

不依赖第三方分词库：
- 英文/标识符按非字母数字切分，再拆驼峰与下划线；
- 中文按连续汉字切分，生成二元组（单字时保留单字）；
- 屏蔽常见英文停用词，避免 BM25 被 "the/and" 之类占满。
"""

from __future__ import annotations

import re

_WORD_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]*|\d+|[^\x00-\x7f]+")
_CAMEL_RE = re.compile(r"(?<=[a-z0-9])(?=[A-Z])|(?<=[A-Z])(?=[A-Z][a-z])")
_CHINESE_RE = re.compile(r"[\u4e00-\u9fff]+")
_COMMENT_RE = re.compile(r"//[^\n]*|/\*.*?\*/", re.S)

STOPWORDS = frozenset(
    """
    a an and are as at be been before but by can could do does for from had has have
    if in into is it its may might must no not of on or our should so such than that
    the their them then there these they this those to under up was were what when
    where which while who will with would you your via allow allows allowed
    """.split()
)


def normalize_code(text: str) -> str:
    """去掉注释与空白，仅保留字母数字下划线与中文，便于哈希与片段比较。"""
    text = _COMMENT_RE.sub(" ", text or "")
    return re.sub(r"[^0-9A-Za-z_\u4e00-\u9fff]+", "", text).lower()


def split_identifier(name: str) -> list[str]:
    """把 showAuthoredFiles / show_authored_files 拆成小写子词。"""
    parts: list[str] = []
    for chunk in re.split(r"[_\W]+", name):
        if not chunk:
            continue
        parts.extend(part.lower() for part in _CAMEL_RE.split(chunk) if part)
    return parts


def tokenize(text: str) -> list[str]:
    """统一的检索分词入口：英文单词/标识符 + 中文二元组。"""
    tokens: list[str] = []
    for raw in _WORD_RE.findall(text or ""):
        if _CHINESE_RE.fullmatch(raw):
            if len(raw) == 1:
                tokens.append(raw)
            else:
                tokens.extend(raw[index : index + 2] for index in range(len(raw) - 1))
            continue

        lowered = raw.lower()
        if lowered.isdigit():
            if len(lowered) <= 6:
                tokens.append(lowered)
            continue

        if lowered in STOPWORDS:
            continue
        tokens.append(lowered)
        pieces = split_identifier(raw)
        if len(pieces) > 1:
            tokens.extend(
                piece for piece in pieces if len(piece) >= 2 and piece not in STOPWORDS
            )
    return tokens


def sha1_hex(text: str) -> str:
    import hashlib

    return hashlib.sha1(text.encode("utf-8", errors="ignore")).hexdigest()
