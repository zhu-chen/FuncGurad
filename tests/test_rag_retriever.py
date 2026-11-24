"""底线：RAG 检索必须能加载索引、遵守 top-k 与过滤条件，缺索引或空查询时给可读错误而不是崩溃。"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from rag.bm25 import BM25Index
from rag.cards import card_text, make_card
from rag.retriever import (
    KnowledgeIndexError,
    KnowledgeRetriever,
    get_retriever,
    reset_retriever,
)
from rag.text import tokenize

HIT_FIELDS = {
    "ref",
    "score",
    "source_idx",
    "project",
    "cve",
    "cwe",
    "func_signature",
    "summary",
    "retriever",
}


def _records() -> list[dict]:
    return [
        {
            "idx": 0,
            "project": "openssl",
            "cve": "CVE-2021-3711",
            "cwe": ["CWE-787"],
            "commit_message": "fix buffer overflow in sm2 decrypt",
            "cve_desc": "buffer overflow when decrypting sm2 ciphertext",
            "func": "int sm2_decrypt(char *dst, const char *src) { return 0; }",
        },
        {
            "idx": 1,
            "project": "linux",
            "cve": "CVE-2022-0001",
            "cwe": ["CWE-416"],
            "commit_message": "fix use after free in netfilter",
            "cve_desc": "use after free in netfilter packet path",
            "func": "void netfilter_free(struct sk_buff *skb) { }",
        },
        {
            "idx": 2,
            "project": "openssl",
            "cve": "CVE-2023-0002",
            "cwe": ["CWE-416"],
            "commit_message": "fix double free in parser",
            "cve_desc": "double free while parsing asn1 input",
            "func": "void parser_free(struct node *n) { }",
        },
    ]


def _build_index(index_dir: Path, records: list[dict] | None = None) -> Path:
    index_dir.mkdir(parents=True, exist_ok=True)
    cards = [make_card(record) for record in (records or _records())]
    with (index_dir / "cards.jsonl").open("w", encoding="utf-8", newline="\n") as stream:
        for card in cards:
            stream.write(json.dumps(card, ensure_ascii=False) + "\n")
    BM25Index.build([tokenize(card_text(card)) for card in cards]).save(
        index_dir / "bm25.json"
    )
    (index_dir / "manifest.json").write_text(
        json.dumps({"cards": len(cards)}, ensure_ascii=False), encoding="utf-8"
    )
    return index_dir


@pytest.fixture()
def index_dir(tmp_path: Path) -> Path:
    return _build_index(tmp_path / "knowledge")


def test_loads_index_and_reports_stats(index_dir):
    retriever = KnowledgeRetriever(index_dir)

    stats = retriever.stats()
    assert stats["cards"] == 3
    assert stats["bm25"]["n_docs"] == 3
    assert stats["manifest"]["cards"] == 3


def test_search_returns_contract_fields(index_dir):
    result = KnowledgeRetriever(index_dir).search("buffer overflow", top_k=1)

    assert result["retriever"] == "bm25"
    assert result["top_k"] == 1
    assert len(result["hits"]) == 1
    hit = result["hits"][0]
    assert set(hit) == HIT_FIELDS
    assert hit["project"] == "openssl"
    assert hit["cwe"] == ["CWE-787"]
    assert hit["ref"] == "knowledge:0"


def test_top_k_is_respected_and_clamped(index_dir):
    retriever = KnowledgeRetriever(index_dir)

    assert len(retriever.search("free", top_k=2)["hits"]) <= 2
    clamped = retriever.search("free", top_k=999)
    assert clamped["top_k"] == 20


def test_project_and_cwe_filters(index_dir):
    retriever = KnowledgeRetriever(index_dir)

    by_project = retriever.search("free", project="linux")
    assert by_project["hits"]
    assert {hit["project"] for hit in by_project["hits"]} == {"linux"}

    by_cwe = retriever.search("free", cwe="cwe-416")
    assert {hit["cve"] for hit in by_cwe["hits"]} == {"CVE-2022-0001", "CVE-2023-0002"}


def test_filter_without_matches_returns_empty(index_dir):
    result = KnowledgeRetriever(index_dir).search("free", project="nginx")

    assert result["hits"] == []
    assert result["candidates"] == 0


def test_empty_query_raises_readable_error(index_dir):
    with pytest.raises(KnowledgeIndexError, match="query 不能为空"):
        KnowledgeRetriever(index_dir).search("   ")


def test_missing_index_raises_readable_error(tmp_path):
    missing = tmp_path / "no-index"

    with pytest.raises(KnowledgeIndexError) as excinfo:
        KnowledgeRetriever(missing)

    message = str(excinfo.value)
    assert "尚未构建" in message
    assert "cards.jsonl" in message


def test_card_count_mismatch_is_rejected(tmp_path):
    index_dir = tmp_path / "broken"
    index_dir.mkdir()
    cards = [make_card(_records()[0])]
    (index_dir / "cards.jsonl").write_text(
        json.dumps(cards[0], ensure_ascii=False) + "\n", encoding="utf-8"
    )
    BM25Index.build(
        [tokenize(card_text(card)) for card in [make_card(record) for record in _records()]]
    ).save(index_dir / "bm25.json")

    with pytest.raises(KnowledgeIndexError, match="不一致"):
        KnowledgeRetriever(index_dir)


def test_get_retriever_caches_until_reset(index_dir):
    reset_retriever()
    try:
        first = get_retriever(index_dir)
        second = get_retriever(index_dir)
        assert first is second

        reset_retriever()
        third = get_retriever(index_dir)
        assert third is not first
    finally:
        reset_retriever()
