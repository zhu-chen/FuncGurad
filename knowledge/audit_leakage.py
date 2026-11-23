"""P3 知识库与测试集泄漏审计。

检查维度：
1. idx 交集；
2. commit 交集（从 commit_url 提取 hash，并兼容 knowledge 的 commit_id）；
3. CVE 交集（测试集无 cve 字段，改为扫描函数文本中的 CVE 提及）；
4. 函数内容规范化哈希是否全等；
5. 函数内容 8-gram 片段包含度（近似重复）。

用法：
    python -m knowledge.audit_leakage --knowledge <knowledge.jsonl> \
        --test cache/dataset/test.jsonl --out cache/knowledge/audit

结果写 cache/knowledge/audit/leakage-audit.{json,md}；脚本本身入库。
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from rag.text import normalize_code, sha1_hex
from repo_service.config import REPO_ROOT

COMMIT_RE = re.compile(r"/commit/([0-9a-f]{7,40})")
CVE_RE = re.compile(r"CVE-\d{4}-\d{4,7}", re.I)
SHINGLE_SIZE = 8
NEAR_DUP_CAP = 4000  # 单条函数参与近似比较的最大规范化字符数


def _knowledge_default() -> Path | None:
    raw = os.getenv("FUNCGUARD_KNOWLEDGE_PATH", "").strip()
    return Path(raw) if raw else None


def commit_of(record: dict[str, Any]) -> str | None:
    match = COMMIT_RE.search(str(record.get("commit_url") or ""))
    if match:
        return match.group(1).lower()
    value = str(record.get("commit_id") or "").strip().lower()
    return value or None


def load_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    with Path(path).open("r", encoding="utf-8", errors="ignore") as stream:
        for line in stream:
            if not line.strip():
                continue
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return rows


def shingles(text: str) -> set[str]:
    text = text[:NEAR_DUP_CAP]
    if len(text) < SHINGLE_SIZE:
        return {text} if text else set()
    return {text[index : index + SHINGLE_SIZE] for index in range(len(text) - SHINGLE_SIZE + 1)}


def near_duplicate_scan(
    knowledge: list[dict[str, Any]], test: list[dict[str, Any]]
) -> dict[str, Any]:
    """用测试集的 8-gram 倒排表给知识库候选打分，报告包含度分布。"""
    test_sets: list[set[str]] = []
    postings: dict[str, list[int]] = {}
    for test_id, record in enumerate(test):
        grams = shingles(normalize_code(record.get("func") or ""))
        test_sets.append(grams)
        for gram in grams:
            postings.setdefault(gram, []).append(test_id)

    best: dict[int, tuple[float, int]] = {}
    for knowledge_id, record in enumerate(knowledge):
        grams = shingles(normalize_code(record.get("func") or ""))
        if not grams:
            continue
        shared: dict[int, int] = {}
        for gram in grams:
            for test_id in postings.get(gram, ()):
                shared[test_id] = shared.get(test_id, 0) + 1
        for test_id, count in shared.items():
            denominator = min(len(grams), len(test_sets[test_id]))
            if denominator <= 0:
                continue
            containment = count / denominator
            previous = best.get(test_id)
            if previous is None or containment > previous[0]:
                best[test_id] = (containment, knowledge_id)

    pairs = [
        {
            "test_idx": test[test_id].get("idx"),
            "knowledge_idx": knowledge[doc_id].get("idx"),
            "knowledge_project": knowledge[doc_id].get("project"),
            "knowledge_cve": knowledge[doc_id].get("cve"),
            "containment": round(score, 4),
        }
        for test_id, (score, doc_id) in best.items()
    ]
    pairs.sort(key=lambda item: item["containment"], reverse=True)

    ge_high = [item for item in pairs if item["containment"] >= 0.8]
    mid = [item for item in pairs if 0.5 <= item["containment"] < 0.8]
    return {
        "shingle_size": SHINGLE_SIZE,
        "cap_chars": NEAR_DUP_CAP,
        "compared_pairs": len(pairs),
        "pairs_ge_0.8": len(ge_high),
        "pairs_0.5_to_0.8": len(mid),
        "max_containment": pairs[0]["containment"] if pairs else 0.0,
        "top_pairs": pairs[:5],
        "high_similarity_pairs": ge_high,
        "high_similarity_knowledge_idxs": sorted(
            {item["knowledge_idx"] for item in ge_high}
        ),
        "mid_similarity_pairs": mid[:10],
    }


def audit(knowledge_path: Path, test_path: Path, out_dir: Path) -> dict[str, Any]:
    started = time.perf_counter()
    knowledge = load_jsonl(knowledge_path)
    test = load_jsonl(test_path)

    knowledge_idx = {row.get("idx") for row in knowledge if isinstance(row.get("idx"), int)}
    test_idx = {row.get("idx") for row in test if isinstance(row.get("idx"), int)}
    idx_overlap = sorted(knowledge_idx & test_idx)

    knowledge_commits = {commit_of(row) for row in knowledge} - {None}
    test_commits = {commit_of(row) for row in test} - {None}
    commit_overlap = sorted(knowledge_commits & test_commits)

    knowledge_cves = {
        str(row.get("cve") or "").upper() for row in knowledge if row.get("cve")
    } - {""}
    test_cves: set[str] = set()
    for row in test:
        test_cves.update(match.upper() for match in CVE_RE.findall(str(row.get("func") or "")))
    cve_text_overlap = sorted(knowledge_cves & test_cves)

    knowledge_hashes = {
        sha1_hex(normalize_code(row.get("func") or ""))
        for row in knowledge
        if row.get("func")
    }
    test_hashes = {
        sha1_hex(normalize_code(row.get("func") or "")) for row in test if row.get("func")
    }
    hash_overlap = sorted(knowledge_hashes & test_hashes)

    near_dup = near_duplicate_scan(knowledge, test)
    elapsed = round(time.perf_counter() - started, 2)

    no_overlap: list[str] = []
    manual: list[str] = []
    if not idx_overlap:
        no_overlap.append("idx：知识库与测试集无任何相同 idx")
    if not commit_overlap:
        no_overlap.append("commit：从 commit_url 提取的提交哈希无交集")
    if not cve_text_overlap:
        no_overlap.append("cve 文本提及：测试集函数中未出现知识库 CVE 编号")
    if not hash_overlap:
        no_overlap.append("函数规范化哈希：无完全相同的函数内容")
    manual.append(
        "CVE 编号无法直接比对：test.jsonl 只有 idx/commit_url/func/target，"
        "没有 cve 字段；已用函数文本中的 CVE 提及做替代检查"
    )
    if near_dup["pairs_0.5_to_0.8"] or near_dup["pairs_ge_0.8"]:
        manual.append(
            f"近似片段：有 {near_dup['pairs_ge_0.8']} 对包含度≥0.8、"
            f"{near_dup['pairs_0.5_to_0.8']} 对在 0.5~0.8，需要人工抽查是否语义泄漏"
        )
    else:
        manual.append(
            f"近似片段：最高包含度 {near_dup['max_containment']}，低于 0.5 阈值，"
            "未发现需要人工抽查的高相似对"
        )

    report: dict[str, Any] = {
        "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "inputs": {
            "knowledge": {
                "name": knowledge_path.name,
                "rows": len(knowledge),
                "size_bytes": knowledge_path.stat().st_size,
            },
            "test": {
                "name": test_path.name,
                "rows": len(test),
                "size_bytes": test_path.stat().st_size,
            },
        },
        "checks": {
            "idx": {
                "knowledge_unique": len(knowledge_idx),
                "test_unique": len(test_idx),
                "overlap": idx_overlap[:20],
                "overlap_count": len(idx_overlap),
            },
            "commit": {
                "knowledge_unique": len(knowledge_commits),
                "test_unique": len(test_commits),
                "overlap": commit_overlap[:20],
                "overlap_count": len(commit_overlap),
            },
            "cve_direct": {
                "available": False,
                "reason": "test.jsonl 无 cve 字段，无法直接比对 CVE 编号",
                "knowledge_unique": len(knowledge_cves),
                "test_cves_found_in_func": sorted(test_cves)[:20],
                "text_overlap": cve_text_overlap,
            },
            "normalized_hash": {
                "knowledge_unique": len(knowledge_hashes),
                "test_unique": len(test_hashes),
                "overlap": hash_overlap,
                "overlap_count": len(hash_overlap),
            },
            "near_duplicate": near_dup,
        },
        "conclusion": {
            "no_overlap_dimensions": no_overlap,
            "manual_review_dimensions": manual,
        },
        "elapsed_seconds": elapsed,
    }
    return report


def render_markdown(report: dict[str, Any]) -> str:
    checks = report["checks"]
    near = checks["near_duplicate"]
    inputs = report["inputs"]
    lines = [
        "# P3 知识库泄漏审计报告",
        "",
        f"- 生成时间：{report['generated_at']}",
        f"- 知识库：`{inputs['knowledge']['name']}`，{inputs['knowledge']['rows']} 行，"
        f"{inputs['knowledge']['size_bytes'] / 1024 / 1024:.1f} MB",
        f"- 测试集：`{inputs['test']['name']}`，{inputs['test']['rows']} 行",
        f"- 审计耗时：{report['elapsed_seconds']}s",
        "",
        "## 结论",
        "",
        "以下维度**无交集**：",
        "",
    ]
    lines += [f"- {item}" for item in report["conclusion"]["no_overlap_dimensions"]] or ["- 无"]
    lines += ["", "以下维度**需要人工确认**：", ""]
    lines += [f"- {item}" for item in report["conclusion"]["manual_review_dimensions"]]

    lines += [
        "",
        "## 明细",
        "",
        "| 维度 | 知识库 | 测试集 | 交集 |",
        "|---|---|---|---|",
        f"| idx | {checks['idx']['knowledge_unique']} | {checks['idx']['test_unique']} | "
        f"{checks['idx']['overlap_count']} |",
        f"| commit | {checks['commit']['knowledge_unique']} | {checks['commit']['test_unique']} | "
        f"{checks['commit']['overlap_count']} |",
        f"| CVE（直接） | {checks['cve_direct']['knowledge_unique']} | 无字段 | 不可比 |",
        f"| CVE（函数文本提及） | - | {len(checks['cve_direct']['test_cves_found_in_func'])} | "
        f"{len(checks['cve_direct']['text_overlap'])} |",
        f"| 规范化哈希 | {checks['normalized_hash']['knowledge_unique']} | "
        f"{checks['normalized_hash']['test_unique']} | "
        f"{checks['normalized_hash']['overlap_count']} |",
        "",
        "## 近似重复（8-gram 包含度）",
        "",
        f"- 比较：每条测试函数 vs 全部知识库函数，单条最多截取 {near['cap_chars']} 个规范化字符",
        f"- ≥0.80：{near['pairs_ge_0.8']} 对；0.50~0.80：{near['pairs_0.5_to_0.8']} 对",
        f"- 最高包含度：{near['max_containment']}",
        "",
    ]
    if near["high_similarity_pairs"]:
        lines += ["高相似样例：", ""]
        for item in near["high_similarity_pairs"]:
            lines.append(
                f"- test idx {item['test_idx']} ↔ knowledge {item['knowledge_project']} "
                f"{item['knowledge_cve']}（{item['containment']}）"
            )
        lines.append("")
    lines += [
        "## 复现",
        "",
        "```powershell",
        "python -m knowledge.audit_leakage --knowledge <knowledge.jsonl> "
        "--test cache\\dataset\\test.jsonl",
        "```",
        "",
    ]
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="知识库与测试集泄漏审计")
    parser.add_argument("--knowledge", type=Path)
    parser.add_argument(
        "--test", type=Path, default=REPO_ROOT / "cache" / "dataset" / "test.jsonl"
    )
    parser.add_argument(
        "--out", type=Path, default=REPO_ROOT / "cache" / "knowledge" / "audit"
    )
    args = parser.parse_args(argv)

    knowledge_path = args.knowledge or _knowledge_default()
    if knowledge_path is None:
        parser.error("必须用 --knowledge 指定知识库文件，或设置 FUNCGUARD_KNOWLEDGE_PATH")
    if not Path(knowledge_path).exists():
        parser.error(f"知识库文件不存在：{knowledge_path}")
    if not Path(args.test).exists():
        parser.error(f"测试集不存在：{args.test}")

    report = audit(Path(knowledge_path), Path(args.test), Path(args.out))
    args.out.mkdir(parents=True, exist_ok=True)
    json_path = args.out / "leakage-audit.json"
    md_path = args.out / "leakage-audit.md"
    json_path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    md_path.write_text(render_markdown(report) + "\n", encoding="utf-8")

    checks = report["checks"]
    print(f"idx 交集：{checks['idx']['overlap_count']}")
    print(f"commit 交集：{checks['commit']['overlap_count']}")
    print(f"CVE 文本提及交集：{len(checks['cve_direct']['text_overlap'])}")
    print(f"规范化哈希交集：{checks['normalized_hash']['overlap_count']}")
    print(
        f"近似片段：≥0.8 {checks['near_duplicate']['pairs_ge_0.8']} 对，"
        f"0.5~0.8 {checks['near_duplicate']['pairs_0.5_to_0.8']} 对，"
        f"最高 {checks['near_duplicate']['max_containment']}"
    )
    print(f"审计耗时：{report['elapsed_seconds']}s")
    print(f"报告：{json_path} / {md_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
