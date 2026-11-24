"""底线：数据未就绪时 API 必须返回可读的降级/503，而不是 500 或静默成功；有数据时结构保持稳定。"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from rag.bm25 import BM25Index
from rag.cards import card_text, make_card
from rag.retriever import reset_retriever
from rag.text import tokenize


def _write_dataset(path: Path) -> Path:
    records = [
        {"idx": 0, "target": 1, "commit_url": "https://example.test/c/0", "func": "int f(void){}"},
        {"idx": 1, "target": 0, "commit_url": "https://example.test/c/1", "func": "int f(void){}"},
    ]
    path.write_text(
        "".join(json.dumps(item, ensure_ascii=False) + "\n" for item in records),
        encoding="utf-8",
    )
    return path


def _build_knowledge(path: Path) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    cards = [
        make_card(
            {
                "idx": 0,
                "project": "openssl",
                "cve": "CVE-2021-3711",
                "cwe": ["CWE-787"],
                "cve_desc": "buffer overflow in sm2 decrypt",
                "func": "int sm2_decrypt(char *dst, const char *src) { return 0; }",
            }
        )
    ]
    with (path / "cards.jsonl").open("w", encoding="utf-8", newline="\n") as stream:
        for card in cards:
            stream.write(json.dumps(card, ensure_ascii=False) + "\n")
    BM25Index.build([tokenize(card_text(card)) for card in cards]).save(path / "bm25.json")
    return path


def _client(tmp_path: Path, monkeypatch, dataset: Path, knowledge: Path | None = None):
    from fastapi.testclient import TestClient

    from backend.api.app import create_app

    monkeypatch.setenv("FUNCGUARD_CACHE_DIR", str(tmp_path / "cache"))
    monkeypatch.setenv("FUNCGUARD_AGENT_RUNS_DIR", str(tmp_path / "runs"))
    monkeypatch.setenv("FUNCGUARD_DATASET", str(dataset))
    monkeypatch.setenv("FUNCGUARD_KNOWLEDGE_DIR", str(knowledge or tmp_path / "no-knowledge"))
    monkeypatch.setenv("FUNCGUARD_LLM_PROVIDER", "stub")
    reset_retriever()
    return TestClient(create_app())


@pytest.fixture(autouse=True)
def _clear_retriever_cache():
    reset_retriever()
    yield
    reset_retriever()


def test_health_degrades_when_dataset_missing(tmp_path, monkeypatch):
    client = _client(tmp_path, monkeypatch, dataset=tmp_path / "missing.jsonl")

    response = client.get("/api/health")

    assert response.status_code == 200
    body = response.json()
    assert body["ok"] is False
    assert body["data"]["status"] == "degraded"
    assert body["data"]["missing"]
    assert "缺少测试集" in body["data"]["message"]


def test_create_run_returns_503_when_dataset_missing(tmp_path, monkeypatch):
    client = _client(tmp_path, monkeypatch, dataset=tmp_path / "missing.jsonl")

    response = client.post("/api/runs", json={"idx": 0})

    assert response.status_code == 503
    body = response.json()
    assert body["ok"] is False
    assert body["error"]["code"] == "data_not_ready"
    assert "测试集" in body["error"]["message"]


def test_knowledge_search_returns_503_when_index_missing(tmp_path, monkeypatch):
    client = _client(
        tmp_path, monkeypatch, dataset=tmp_path / "missing.jsonl", knowledge=None
    )

    response = client.get("/api/knowledge/search", params={"q": "buffer overflow"})

    assert response.status_code == 503
    body = response.json()
    assert body["ok"] is False
    assert body["error"]["code"] == "knowledge_not_ready"
    assert "尚未构建" in body["error"]["message"]


def test_health_and_pairs_with_ready_dataset(tmp_path, monkeypatch):
    dataset = _write_dataset(tmp_path / "test.jsonl")
    client = _client(tmp_path, monkeypatch, dataset=dataset, knowledge=_build_knowledge(tmp_path / "knowledge"))

    health = client.get("/api/health")
    assert health.status_code == 200
    body = health.json()
    assert body["ok"] is True
    assert body["data"]["status"] == "ok"
    assert body["data"]["dataset"]["samples"] == 2
    assert body["data"]["dataset"]["pairs"] == 1

    pairs = client.get("/api/pairs")
    assert pairs.status_code == 200
    items = pairs.json()["data"]["pairs"]
    assert len(items) == 1
    assert items[0]["vulnerable_idx"] == 0
    assert items[0]["fixed_idx"] == 1


def test_knowledge_search_with_ready_index(tmp_path, monkeypatch):
    knowledge = _build_knowledge(tmp_path / "knowledge")
    client = _client(
        tmp_path, monkeypatch, dataset=tmp_path / "missing.jsonl", knowledge=knowledge
    )

    response = client.get("/api/knowledge/search", params={"q": "buffer overflow", "top_k": 1})

    assert response.status_code == 200
    body = response.json()
    assert body["ok"] is True
    assert body["data"]["retriever"] == "bm25"
    assert len(body["data"]["hits"]) == 1
    assert body["data"]["hits"][0]["project"] == "openssl"


def test_run_validation_error_is_readable(tmp_path, monkeypatch):
    dataset = _write_dataset(tmp_path / "test.jsonl")
    client = _client(tmp_path, monkeypatch, dataset=dataset)

    response = client.post("/api/runs", json={"idx": -1})

    assert response.status_code == 422
    body = response.json()
    assert body["ok"] is False
    assert body["error"]["code"] == "validation_error"
    assert "请求参数不合法" in body["error"]["message"]


def test_unknown_idx_is_rejected_by_job(tmp_path, monkeypatch):
    dataset = _write_dataset(tmp_path / "test.jsonl")
    client = _client(tmp_path, monkeypatch, dataset=dataset)

    response = client.post("/api/runs", json={"idx": 99})

    assert response.status_code == 400
    assert "不在测试集中" in response.json()["error"]["message"]
