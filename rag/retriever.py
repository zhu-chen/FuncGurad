"""知识库检索：加载 BM25 索引，按 KnowledgeHit 契约返回命中。

契约对齐 shared/contracts/knowledge_hit.schema.json：
每条命中的字段固定为 ref / score / source_idx / project / cve / cwe /
func_signature / summary / retriever，不额外增加字段。
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any

from rag.bm25 import BM25Index
from rag.cards import hit_summary, read_cards
from rag.text import tokenize
from repo_service.config import REPO_ROOT

RETRIEVER_NAME = "bm25"
MAX_TOP_K = 20


class KnowledgeIndexError(RuntimeError):
    """索引缺失或损坏；消息里带可读的重建提示。"""


def default_index_dir() -> Path:
    return Path(
        os.getenv("FUNCGUARD_KNOWLEDGE_DIR", str(REPO_ROOT / "cache" / "knowledge"))
    ).resolve()


class KnowledgeRetriever:
    def __init__(self, index_dir: Path | None = None) -> None:
        self.index_dir = Path(index_dir or default_index_dir()).resolve()
        cards_path = self.index_dir / "cards.jsonl"
        bm25_path = self.index_dir / "bm25.json"
        manifest_path = self.index_dir / "manifest.json"

        missing = [
            str(path)
            for path in (cards_path, bm25_path)
            if not path.exists()
        ]
        if missing:
            raise KnowledgeIndexError(
                "知识库索引尚未构建（缺少 "
                + "、".join(missing)
                + "）。请先运行 python -m rag.build_index --knowledge <knowledge.jsonl>，"
                "详见 docs/handoff/2026-11-23-p3-knowledge-rag.md。"
            )

        self.cards = read_cards(cards_path)
        self.bm25 = BM25Index.load(bm25_path)
        if len(self.cards) != self.bm25.n_docs:
            raise KnowledgeIndexError(
                f"卡片数 {len(self.cards)} 与索引文档数 {self.bm25.n_docs} 不一致，"
                "请重新运行 python -m rag.build_index 重建。"
            )
        self.manifest: dict[str, Any] = {}
        if manifest_path.exists():
            try:
                self.manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            except json.JSONDecodeError:
                self.manifest = {}

    def _allowed_docs(self, project: str | None, cwe: str | None) -> set[int] | None:
        """按项目/漏洞类型过滤；两个条件都不传时返回 None 表示不过滤。"""
        project_key = (project or "").strip().lower()
        cwe_key = (cwe or "").strip().upper()
        if not project_key and not cwe_key:
            return None

        allowed: set[int] = set()
        for doc_id, card in enumerate(self.cards):
            if project_key and (card.get("project") or "").lower() != project_key:
                continue
            if cwe_key:
                cwes = [item.upper() for item in card.get("cwe") or []]
                if cwe_key not in cwes:
                    continue
            allowed.add(doc_id)
        return allowed

    def search(
        self,
        query: str,
        top_k: int = 5,
        project: str | None = None,
        cwe: str | None = None,
    ) -> dict[str, Any]:
        query = (query or "").strip()
        if not query:
            raise KnowledgeIndexError("query 不能为空")

        top_k = max(1, min(int(top_k or 5), MAX_TOP_K))
        started = time.perf_counter()
        allowed = self._allowed_docs(project, cwe)
        if allowed is not None and not allowed:
            hits: list[dict[str, Any]] = []
        else:
            tokens = tokenize(query)
            ranked = self.bm25.search(tokens, top_k=top_k, allowed=allowed)
            hits = [self._hit(doc_id, score) for doc_id, score in ranked]
        elapsed_ms = round((time.perf_counter() - started) * 1000, 2)

        return {
            "query": query,
            "retriever": RETRIEVER_NAME,
            "top_k": top_k,
            "filters": {"project": project or None, "cwe": cwe or None},
            "candidates": len(allowed) if allowed is not None else self.bm25.n_docs,
            "hits": hits,
            "elapsed_ms": elapsed_ms,
        }

    def _hit(self, doc_id: int, score: float) -> dict[str, Any]:
        card = self.cards[doc_id]
        return {
            "ref": card["ref"],
            "score": round(float(score), 4),
            "source_idx": int(card["source_idx"]),
            "project": card.get("project"),
            "cve": card.get("cve"),
            "cwe": list(card.get("cwe") or []),
            "func_signature": card.get("func_signature") or None,
            "summary": hit_summary(card),
            "retriever": RETRIEVER_NAME,
        }

    def stats(self) -> dict[str, Any]:
        return {
            "index_dir": str(self.index_dir),
            "cards": len(self.cards),
            "bm25": self.bm25.stats(),
            "manifest": self.manifest,
        }


_CACHE: dict[Path, KnowledgeRetriever] = {}


def get_retriever(index_dir: Path | None = None) -> KnowledgeRetriever:
    """进程内复用索引；重建索引后需重启进程（或调用 reset_retriever）。"""
    path = Path(index_dir or default_index_dir()).resolve()
    retriever = _CACHE.get(path)
    if retriever is None:
        retriever = KnowledgeRetriever(path)
        _CACHE[path] = retriever
    return retriever


def reset_retriever() -> None:
    _CACHE.clear()
