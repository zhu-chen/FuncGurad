"""纯 Python BM25 实现：倒排索引 + Okapi BM25 打分。

不依赖 numpy/第三方检索库；索引以 JSON 落盘，便于直接检查与重建。
"""

from __future__ import annotations

import json
import math
from collections import Counter
from pathlib import Path
from typing import Iterable


class BM25Index:
    def __init__(
        self,
        postings: dict[str, list[list[int]]],
        doc_len: list[int],
        n_docs: int,
        avgdl: float,
        k1: float = 1.5,
        b: float = 0.75,
    ) -> None:
        self.postings = postings
        self.doc_len = doc_len
        self.n_docs = n_docs
        self.avgdl = avgdl or 1.0
        self.k1 = k1
        self.b = b
        self._idf_cache: dict[str, float] = {}

    @classmethod
    def build(
        cls,
        docs_tokens: Iterable[list[str]],
        k1: float = 1.5,
        b: float = 0.75,
    ) -> "BM25Index":
        postings: dict[str, list[list[int]]] = {}
        doc_len: list[int] = []
        for doc_id, tokens in enumerate(docs_tokens):
            doc_len.append(len(tokens))
            for term, tf in Counter(tokens).items():
                postings.setdefault(term, []).append([doc_id, tf])
        avgdl = (sum(doc_len) / len(doc_len)) if doc_len else 0.0
        return cls(postings, doc_len, len(doc_len), avgdl, k1=k1, b=b)

    def idf(self, term: str) -> float:
        cached = self._idf_cache.get(term)
        if cached is not None:
            return cached
        df = len(self.postings.get(term) or ())
        value = math.log(1 + (self.n_docs - df + 0.5) / (df + 0.5))
        self._idf_cache[term] = value
        return value

    def search(
        self,
        tokens: list[str],
        top_k: int = 5,
        allowed: set[int] | None = None,
    ) -> list[tuple[int, float]]:
        """返回 [(doc_id, score)]，按分数倒序；allowed 用于项目/漏洞类型过滤。"""
        if top_k <= 0 or not tokens:
            return []
        scores: dict[int, float] = {}
        for term in set(tokens):
            entries = self.postings.get(term)
            if not entries:
                continue
            idf = self.idf(term)
            for doc_id, tf in entries:
                if allowed is not None and doc_id not in allowed:
                    continue
                dl = self.doc_len[doc_id] or 1
                denom = tf + self.k1 * (1 - self.b + self.b * dl / self.avgdl)
                scores[doc_id] = scores.get(doc_id, 0.0) + idf * tf * (self.k1 + 1) / denom
        ranked = sorted(scores.items(), key=lambda item: item[1], reverse=True)
        return ranked[:top_k]

    def save(self, path: Path) -> None:
        payload = {
            "version": 1,
            "k1": self.k1,
            "b": self.b,
            "n_docs": self.n_docs,
            "avgdl": self.avgdl,
            "doc_len": self.doc_len,
            "postings": self.postings,
        }
        Path(path).write_text(
            json.dumps(payload, ensure_ascii=False, separators=(",", ":")),
            encoding="utf-8",
        )

    @classmethod
    def load(cls, path: Path) -> "BM25Index":
        payload = json.loads(Path(path).read_text(encoding="utf-8"))
        return cls(
            postings=payload["postings"],
            doc_len=payload["doc_len"],
            n_docs=payload["n_docs"],
            avgdl=payload["avgdl"],
            k1=payload.get("k1", 1.5),
            b=payload.get("b", 0.75),
        )

    def stats(self) -> dict[str, int | float]:
        return {
            "n_docs": self.n_docs,
            "terms": len(self.postings),
            "postings": sum(len(entries) for entries in self.postings.values()),
            "avgdl": round(self.avgdl, 2),
        }
