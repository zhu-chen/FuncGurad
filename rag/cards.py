"""知识库清洗与卡片化：把 knowledge.jsonl 的每条记录整理成可检索单元。

设计要点：
- 字段全部做了空值兜底，个别脏记录只跳过或留空，不中断整批；
- 同一 idx 出现多次时保留首条，其余计入 duplicates；
- 卡片是纯本地中间产物，只写 cache/knowledge/，不入库。
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Iterator

FUNC_EXCERPT_CHARS = 4000
DESC_CHARS = 1200
COMMIT_MESSAGE_CHARS = 600
SIGNATURE_CHARS = 200


class KnowledgeDataError(RuntimeError):
    """知识库文件缺失或不可读。"""


def _text(value: Any) -> str:
    if value is None:
        return ""
    text = str(value).strip()
    return "" if text.lower() in ("none", "null", "nan") else text


def _truncate(text: str, limit: int) -> str:
    return text if len(text) <= limit else text[:limit]


def extract_signature(func: str) -> str:
    """取函数第一段签名：优先截到第一个 '{'，再退化为首行。"""
    if not func:
        return ""
    head = func.split("{", 1)[0].strip()
    if not head:
        head = func.strip().splitlines()[0]
    head = " ".join(head.split())
    return _truncate(head, SIGNATURE_CHARS)


def make_card(record: dict[str, Any]) -> dict[str, Any]:
    """把一条原始记录转换成卡片；缺字段用空值/默认值兜底。"""
    idx = record.get("idx")
    source_idx = idx if isinstance(idx, int) and idx >= 0 else 0
    cwe = record.get("cwe") or []
    if isinstance(cwe, str):
        cwe = [item.strip() for item in cwe.split(",") if item.strip()]
    cwe = [str(item).strip().upper() for item in cwe if str(item).strip()]

    project = _text(record.get("project")) or "unknown"
    cve = _text(record.get("cve")).upper() or None
    func = _text(record.get("func"))
    commit_message = _text(record.get("commit_message"))

    return {
        "ref": f"knowledge:{source_idx}",
        "source_idx": source_idx,
        "project": project,
        "cve": cve,
        "cwe": cwe,
        "commit_id": _text(record.get("commit_id")) or None,
        "commit_message": _truncate(commit_message, COMMIT_MESSAGE_CHARS),
        "target": record.get("target") if record.get("target") in (0, 1) else None,
        "file_name": _text(record.get("file_name")) or None,
        "func_signature": extract_signature(func),
        "func_excerpt": _truncate(func, FUNC_EXCERPT_CHARS),
        "cve_desc": _truncate(_text(record.get("cve_desc")), DESC_CHARS),
        "project_url": _text(record.get("project_url")) or None,
        "commit_url": _text(record.get("commit_url")) or None,
    }


def card_text(card: dict[str, Any]) -> str:
    """拼接用于建索引的文本；项目和漏洞类型重复一次以提升权重。"""
    project = card.get("project") or ""
    cve = card.get("cve") or ""
    cwe = " ".join(card.get("cwe") or [])
    signature = card.get("func_signature") or ""
    parts = [
        project,
        project,
        cve,
        cve,
        cwe,
        cwe,
        signature,
        signature,
        card.get("file_name") or "",
        card.get("cve_desc") or "",
        card.get("commit_message") or "",
        card.get("func_excerpt") or "",
    ]
    return "\n".join(part for part in parts if part)


def hit_summary(card: dict[str, Any]) -> str:
    """面向模型的漏洞模式摘要：只用 CVE 描述与修复提交标题，不回填代码。"""
    desc = card.get("cve_desc") or ""
    if not desc:
        desc = (card.get("commit_message") or "").splitlines()[0] if card.get("commit_message") else ""
    prefix = " ".join(card.get("cwe") or [])
    summary = f"{prefix} {desc}".strip()
    return _truncate(summary, 400)


def iter_cards(path: Path) -> Iterator[tuple[dict[str, Any], dict[str, int]]]:
    """逐行读取并转换，返回 (卡片, 统计)；坏行只计数不抛出。"""
    stats = {"rows": 0, "cards": 0, "duplicates": 0, "bad_lines": 0}
    seen: set[int] = set()
    with Path(path).open("r", encoding="utf-8", errors="ignore") as stream:
        for line in stream:
            if not line.strip():
                continue
            stats["rows"] += 1
            try:
                record = json.loads(line)
            except json.JSONDecodeError:
                stats["bad_lines"] += 1
                continue
            if not isinstance(record, dict):
                stats["bad_lines"] += 1
                continue

            source_idx = record.get("idx")
            if not isinstance(source_idx, int) or source_idx < 0:
                stats["bad_lines"] += 1
                continue
            if source_idx in seen:
                stats["duplicates"] += 1
                continue
            seen.add(source_idx)

            yield make_card(record), stats
            stats["cards"] += 1


def read_cards(path: Path) -> list[dict[str, Any]]:
    cards: list[dict[str, Any]] = []
    with Path(path).open("r", encoding="utf-8") as stream:
        for line in stream:
            if line.strip():
                cards.append(json.loads(line))
    return cards
