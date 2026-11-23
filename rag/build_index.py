"""构建知识库检索索引：清洗卡片 -> 分词 -> BM25 -> manifest。

用法：
    python -m rag.build_index --knowledge <knowledge.jsonl> [--out cache/knowledge]

产物只写本地缓存目录（默认 cache/knowledge/），不入库。
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import os
import sys
import time
from datetime import datetime
from pathlib import Path

from rag.bm25 import BM25Index
from rag.cards import card_text, iter_cards
from rag.text import tokenize
from repo_service.config import REPO_ROOT, display_path


def _default_knowledge_path() -> Path | None:
    raw = os.getenv("FUNCGUARD_KNOWLEDGE_PATH", "").strip()
    return Path(raw) if raw else None


def _detect_vector_backend() -> dict[str, object]:
    """检测是否有可直接离线使用的 embedding 模型；没有就明确记录原因。"""
    model_path = os.getenv("FUNCGUARD_EMBEDDING_MODEL", "").strip()
    has_lib = importlib.util.find_spec("sentence_transformers") is not None
    if model_path and has_lib and Path(model_path).exists():
        return {"enabled": False, "reason": "检测到本地模型但 P3 未启用向量索引（交接受限）"}
    if not has_lib:
        reason = "未安装 sentence-transformers，使用 BM25 单路"
    elif not model_path:
        reason = "未配置 FUNCGUARD_EMBEDDING_MODEL，使用 BM25 单路"
    else:
        reason = f"FUNCGUARD_EMBEDDING_MODEL 指向的目录不存在：{model_path}"
    return {"enabled": False, "reason": reason}


def _read_audit_idxs(path: Path) -> tuple[set[int], float | None]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    near = payload["checks"]["near_duplicate"]
    idxs = payload.get("excluded_knowledge_idxs")
    if not idxs:
        idxs = near.get("excluded_knowledge_idxs") or near.get(
            "high_similarity_knowledge_idxs", []
        )
    threshold = payload.get("threshold") or near.get("threshold")
    return {int(idx) for idx in idxs}, threshold


def _load_leakage_exclusions(
    out_dir: Path,
) -> tuple[set[int], Path | None, float | None, Path | None]:
    """读取审计结果里与测试集近似的 knowledge idx，默认从索引中剔除。

    严格口径下审计脚本会枚举所有 ≥threshold 的配对，并把涉及的 idx 写入
    `excluded_knowledge_idxs`；`high_similarity_knowledge_idxs` 保留为兼容别名。
    若已存在卡片残余审计（leakage-audit-cards.json），把残余命中一并剔除，
    使最终索引达到「任意卡片与测试样本 < 阈值」的不动点。
    """
    audit_path = out_dir / "audit" / "leakage-audit.json"
    cards_audit_path = out_dir / "audit" / "leakage-audit-cards.json"
    if not audit_path.exists():
        return set(), None, None, None
    try:
        idxs, threshold = _read_audit_idxs(audit_path)
    except (json.JSONDecodeError, KeyError, TypeError):
        return set(), audit_path, None, None

    cards_idxs: set[int] = set()
    cards_used: Path | None = None
    if cards_audit_path.exists():
        try:
            cards_idxs, cards_threshold = _read_audit_idxs(cards_audit_path)
        except (json.JSONDecodeError, KeyError, TypeError):
            cards_idxs = set()
        else:
            if threshold is None or cards_threshold == threshold:
                cards_used = cards_audit_path
            else:
                print(
                    f"!! 卡片审计阈值 {cards_threshold} 与知识库审计阈值 {threshold} 不一致，"
                    "忽略卡片审计结果",
                    file=sys.stderr,
                )
                cards_idxs = set()
    return idxs | cards_idxs, audit_path, threshold, cards_used


def build(
    knowledge_path: Path,
    out_dir: Path,
    include_leaky: bool = False,
) -> dict[str, object]:
    if not knowledge_path.exists():
        raise SystemExit(
            f"知识库文件不存在：{knowledge_path}\n"
            "请用 --knowledge 指定 knowledge.jsonl，或设置环境变量 "
            "FUNCGUARD_KNOWLEDGE_PATH。"
        )

    out_dir.mkdir(parents=True, exist_ok=True)
    cards_path = out_dir / "cards.jsonl"
    bm25_path = out_dir / "bm25.json"
    manifest_path = out_dir / "manifest.json"

    started = time.perf_counter()

    excluded_idxs: set[int] = set()
    audit_path: Path | None = None
    cards_audit_path: Path | None = None
    threshold: float | None = None
    if not include_leaky:
        excluded_idxs, audit_path, threshold, cards_audit_path = _load_leakage_exclusions(
            out_dir
        )
        if audit_path is None:
            print(
                "!! 未找到 cache/knowledge/audit/leakage-audit.json，"
                "本次不剔除近似知识条目；建议先运行 python -m knowledge.audit_leakage",
                file=sys.stderr,
            )
        elif threshold is None:
            print(
                "!! 审计结果缺少阈值信息，可能是旧版本产物；"
                "请重新运行 python -m knowledge.audit_leakage",
                file=sys.stderr,
            )

    phase = time.perf_counter()
    cards: list[dict] = []
    stats = {"rows": 0, "cards": 0, "duplicates": 0, "bad_lines": 0}
    excluded_count = 0
    with cards_path.open("w", encoding="utf-8", newline="\n") as stream:
        for card, stats in iter_cards(knowledge_path):
            if card["source_idx"] in excluded_idxs:
                excluded_count += 1
                continue
            cards.append(card)
            stream.write(json.dumps(card, ensure_ascii=False) + "\n")
    cards_seconds = time.perf_counter() - phase

    phase = time.perf_counter()
    corpus_tokens = [tokenize(card_text(card)) for card in cards]
    tokenize_seconds = time.perf_counter() - phase

    phase = time.perf_counter()
    index = BM25Index.build(corpus_tokens)
    index.save(bm25_path)
    index_seconds = time.perf_counter() - phase

    total_seconds = time.perf_counter() - started
    manifest: dict[str, object] = {
        "version": 1,
        "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "source": {
            "name": knowledge_path.name,
            "size_bytes": knowledge_path.stat().st_size,
            "rows": stats["rows"],
            "cards_total": stats["cards"],
            "cards_indexed": len(cards),
            "duplicates": stats["duplicates"],
            "bad_lines": stats["bad_lines"],
        },
        "leakage_filter": {
            "applied": not include_leaky,
            "audit_file": display_path(audit_path) if audit_path else None,
            "cards_audit_file": display_path(cards_audit_path) if cards_audit_path else None,
            "threshold_containment": threshold,
            "excluded": excluded_count,
            "excluded_total_idxs": len(excluded_idxs),
            "excluded_idxs": sorted(excluded_idxs)[:100],
        },
        "retriever": "bm25",
        "index": {**index.stats(), "bytes": bm25_path.stat().st_size},
        "cards_bytes": cards_path.stat().st_size,
        "vector": _detect_vector_backend(),
        "timings_seconds": {
            "cards": round(cards_seconds, 2),
            "tokenize": round(tokenize_seconds, 2),
            "index": round(index_seconds, 2),
            "total": round(total_seconds, 2),
        },
        "cache_dir": display_path(out_dir),
    }
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return manifest


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="构建知识库 BM25 索引")
    parser.add_argument("--knowledge", type=Path, help="knowledge.jsonl 路径")
    parser.add_argument(
        "--out",
        type=Path,
        default=Path(os.getenv("FUNCGUARD_KNOWLEDGE_DIR", str(REPO_ROOT / "cache" / "knowledge"))),
        help="索引输出目录，默认 cache/knowledge",
    )
    parser.add_argument(
        "--include-leaky",
        action="store_true",
        help="保留审计判定为泄漏的知识条目；默认剔除（阈值由审计结果决定，默认 0.5）",
    )
    args = parser.parse_args(argv)

    knowledge_path = args.knowledge or _default_knowledge_path()
    if knowledge_path is None:
        parser.error("必须用 --knowledge 指定知识库文件，或设置 FUNCGUARD_KNOWLEDGE_PATH")

    manifest = build(Path(knowledge_path), Path(args.out), args.include_leaky)
    source = manifest["source"]
    index = manifest["index"]
    timings = manifest["timings_seconds"]
    leakage = manifest["leakage_filter"]
    print(
        f"[cards]      {source['cards_indexed']} 条入索引"
        f"（原始 {source['rows']} 行 / {source['cards_total']} 条卡片，"
        f"重复 {source['duplicates']}，坏行 {source['bad_lines']}，"
        f"泄漏剔除 {leakage['excluded']}） {timings['cards']}s"
    )
    print(
        f"[tokenize]   平均 {index['avgdl']} 词/条，共 {index['postings']} 条倒排 "
        f"{timings['tokenize']}s"
    )
    print(
        f"[bm25]       {index['terms']} 个词项 {timings['index']}s，"
        f"索引 {index['bytes'] / 1024 / 1024:.1f} MB"
    )
    print(f"[vector]     {manifest['vector']['reason']}")
    print(f"[done]       总耗时 {timings['total']}s，输出 {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
