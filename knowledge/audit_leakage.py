"""P3 知识库与测试集泄漏审计（严格口径）。

检查维度：
1. idx 交集；
2. commit 交集（从 commit_url 提取 hash，并兼容 knowledge 的 commit_id）；
3. CVE 交集（测试集无 cve 字段，改为扫描函数文本中的 CVE 提及）；
4. 函数内容规范化哈希是否全等；
5. 函数内容 8-gram 片段包含度（近似重复），默认阈值 0.5。

严格口径：只要知识库记录与**任一**测试样本的包含度 ≥ 阈值，就进入剔除清单，
不再只保留每个测试样本的最佳匹配。

用法：
    python -m knowledge.audit_leakage --knowledge <knowledge.jsonl> --threshold 0.5
    python -m knowledge.audit_leakage --cards --knowledge cache/knowledge/cards.jsonl

结果写 cache/knowledge/audit/leakage-audit.{json,md}；
卡片模式写 leakage-audit-cards.{json,md}。脚本本身入库，产物只写 cache/。
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

from rag.cards import extract_signature
from rag.text import normalize_code, sha1_hex
from repo_service.config import REPO_ROOT, display_path

COMMIT_RE = re.compile(r"/commit/([0-9a-f]{7,40})")
CVE_RE = re.compile(r"CVE-\d{4}-\d{4,7}", re.I)
SHINGLE_SIZE = 8
NEAR_DUP_CAP = 4000  # 单条函数参与近似比较的最大规范化字符数
DEFAULT_THRESHOLD = 0.5


def _knowledge_default() -> Path | None:
    raw = os.getenv("FUNCGUARD_KNOWLEDGE_PATH", "").strip()
    return Path(raw) if raw else None


def _record_idx(record: dict[str, Any]) -> int | None:
    value = record.get("idx")
    if isinstance(value, int):
        return value
    value = record.get("source_idx")
    return value if isinstance(value, int) else None


def _record_func(record: dict[str, Any]) -> str:
    """原始记录用 func，索引卡片用 func_excerpt。"""
    return str(record.get("func") or record.get("func_excerpt") or "")


def _short_signature(func: str) -> str:
    return extract_signature(func)[:160]


def repo_tokens(url: Any) -> set[str]:
    """从 commit/project URL 中提取仓库名，用于判断是否同项目。"""
    text = str(url or "")
    tokens: set[str] = set()
    for pattern in (
        r"github\.com/[^/]+/([^/?#]+)",
        r"[?&]p=([^;&]+)",
        r"/([^/?#]+?)(?:\.git)?/commit/",
    ):
        match = re.search(pattern, text, re.I)
        if match:
            tokens.add(match.group(1))
    return {re.sub(r"[^a-z0-9]", "", token.lower()) for token in tokens if token}


def same_project(project: Any, url: Any) -> bool:
    normalized = re.sub(r"[^a-z0-9]", "", str(project or "").lower())
    if not normalized:
        return False
    return normalized in repo_tokens(url)


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


def gram_sets(func: str) -> list[tuple[str, set[str]]]:
    """同一函数的两种文本形态，从严取最大包含度。

    - full：完整函数规范化后截断 4,000 字符；
    - raw4000：先截断原始函数 4,000 字符再规范化。

    索引卡片保存的是原始函数前 4,000 字符（func_excerpt），长函数在卡片上
    的规范化长度会小于 4,000，包含度可能略高于 full。两种形态都算一遍，
    保证审计结论对「知识库原文」和「索引卡片」同时成立，避免重建索引后
    又冒出新的残余命中。
    """
    full = shingles(normalize_code(func))
    short = shingles(normalize_code(func[:NEAR_DUP_CAP]))
    if short == full:
        return [("full", full)]
    return [("full", full), ("raw4000", short)]


def near_duplicate_scan(
    knowledge: list[dict[str, Any]],
    test: list[dict[str, Any]],
    threshold: float = DEFAULT_THRESHOLD,
) -> dict[str, Any]:
    """枚举所有 ≥threshold 的 (test, knowledge) 配对，并保留每个测试样本的最佳配对。

    - `pairs`：严格口径下的全量配对，用于生成剔除清单；
    - `best_pairs`：每个测试样本的最佳配对，用于与旧的 0.8 口径对比。
    """
    test_grams: list[set[str]] = []
    postings: dict[str, list[int]] = {}
    for test_id, record in enumerate(test):
        grams = shingles(normalize_code(_record_func(record)))
        test_grams.append(grams)
        for gram in grams:
            postings.setdefault(gram, []).append(test_id)

    best: dict[int, tuple[float, int]] = {}
    pairs: list[dict[str, Any]] = []
    for knowledge_id, record in enumerate(knowledge):
        func = _record_func(record)
        # 每个测试样本取两种形态里的最大包含度；每个测试样本的最佳配对
        # 仍由 full 形态决定，保持与旧 0.8 口径可比。
        per_test: dict[int, tuple[float, str]] = {}
        for form, grams in gram_sets(func):
            if not grams:
                continue
            shared: dict[int, int] = {}
            for gram in grams:
                for test_id in postings.get(gram, ()):
                    shared[test_id] = shared.get(test_id, 0) + 1
            for test_id, count in shared.items():
                denominator = min(len(grams), len(test_grams[test_id]))
                if denominator <= 0:
                    continue
                containment = count / denominator
                if form == "full":
                    previous = best.get(test_id)
                    if previous is None or containment > previous[0]:
                        best[test_id] = (containment, knowledge_id)
                current = per_test.get(test_id)
                if current is None or containment > current[0]:
                    per_test[test_id] = (containment, form)
        for test_id, (containment, form) in per_test.items():
            if containment < threshold:
                continue
            test_record = test[test_id]
            test_func = _record_func(test_record)
            knowledge_cve = str(record.get("cve") or "").upper() or None
            mentions = {match.upper() for match in CVE_RE.findall(test_func)}
            pairs.append(
                {
                    "test_idx": test_record.get("idx"),
                    "test_target": test_record.get("target"),
                    "test_project": sorted(repo_tokens(test_record.get("commit_url"))),
                    "test_commit": (commit_of(test_record) or "")[:12] or None,
                    "test_cve_mentions": sorted(mentions)[:5],
                    "test_signature": _short_signature(test_func),
                    "knowledge_idx": _record_idx(record),
                    "knowledge_project": record.get("project"),
                    "knowledge_cve": knowledge_cve,
                    "knowledge_commit": (commit_of(record) or "")[:12] or None,
                    "knowledge_signature": _short_signature(func),
                    "containment": round(containment, 4),
                    "compare_form": form,
                    "same_project": same_project(
                        record.get("project"), test_record.get("commit_url")
                    ),
                    "cve_mention_match": bool(knowledge_cve and knowledge_cve in mentions),
                }
            )

    def pair_of(test_id: int, doc_id: int, score: float) -> dict[str, Any]:
        test_record = test[test_id]
        record = knowledge[doc_id]
        return {
            "test_idx": test_record.get("idx"),
            "knowledge_idx": _record_idx(record),
            "knowledge_project": record.get("project"),
            "knowledge_cve": record.get("cve"),
            "containment": round(score, 4),
        }

    best_pairs = [
        pair_of(test_id, doc_id, score) for test_id, (score, doc_id) in best.items()
    ]
    best_pairs.sort(key=lambda item: item["containment"], reverse=True)

    pairs.sort(
        key=lambda item: (
            -item["containment"],
            str(item["test_idx"]),
            str(item["knowledge_idx"]),
        )
    )

    best_high = [item for item in best_pairs if item["containment"] >= 0.8]
    best_mid = [item for item in best_pairs if 0.5 <= item["containment"] < 0.8]
    all_high = [item for item in pairs if item["containment"] >= 0.8]
    all_mid = [item for item in pairs if item["containment"] < 0.8]
    excluded = sorted(
        {
            int(item["knowledge_idx"])
            for item in pairs
            if isinstance(item["knowledge_idx"], int)
        }
    )

    return {
        "shingle_size": SHINGLE_SIZE,
        "cap_chars": NEAR_DUP_CAP,
        "threshold": threshold,
        "compared_pairs": len(best_pairs),
        "max_containment": best_pairs[0]["containment"] if best_pairs else 0.0,
        # 旧口径（每测试样本最佳配对）下的分布，便于与 0.8 基线对比
        "pairs_ge_0.8": len(best_high),
        "pairs_0.5_to_0.8": len(best_mid),
        "top_pairs": best_pairs[:5],
        "high_similarity_pairs": best_high,
        "mid_similarity_pairs": best_mid,
        # 严格口径（所有 ≥threshold 的配对）
        "pairs_ge_threshold": len(pairs),
        "pairs_ge_threshold_ge_0.8": len(all_high),
        "pairs_ge_threshold_0.5_to_0.8": len(all_mid),
        "excluded_knowledge_count": len(excluded),
        "excluded_knowledge_idxs": excluded,
        "high_similarity_knowledge_idxs": excluded,  # 兼容 build_index 的旧字段名
        "pairs": pairs,
    }


def audit(
    knowledge_path: Path,
    test_path: Path,
    out_dir: Path,
    threshold: float = DEFAULT_THRESHOLD,
    cards_mode: bool = False,
) -> dict[str, Any]:
    started = time.perf_counter()
    knowledge = load_jsonl(knowledge_path)
    test = load_jsonl(test_path)

    knowledge_idx = {
        idx for row in knowledge if (idx := _record_idx(row)) is not None
    }
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
        test_cves.update(
            match.upper() for match in CVE_RE.findall(_record_func(row))
        )
    cve_text_overlap = sorted(knowledge_cves & test_cves)

    knowledge_hashes = {
        sha1_hex(normalize_code(_record_func(row)))
        for row in knowledge
        if _record_func(row)
    }
    test_hashes = {
        sha1_hex(normalize_code(_record_func(row)))
        for row in test
        if _record_func(row)
    }
    hash_overlap = sorted(knowledge_hashes & test_hashes)

    near_dup = near_duplicate_scan(knowledge, test, threshold)
    cve_excluded = sorted(
        {
            idx
            for row in knowledge
            if (idx := _record_idx(row)) is not None
            and str(row.get("cve") or "").upper() in test_cves
        }
    )
    code_excluded = near_dup["excluded_knowledge_idxs"]
    all_excluded = sorted(set(code_excluded) | set(cve_excluded))
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
    if cve_text_overlap:
        manual.append(
            f"CVE 文本提及：测试函数中出现 {len(cve_text_overlap)} 个知识库同编号 "
            f"CVE（{', '.join(cve_text_overlap[:5])}），涉及 {len(cve_excluded)} 条知识记录，"
            "已按严格口径一并剔除"
        )
    else:
        manual.append("CVE 文本提及：知识库中没有测试函数提到的 CVE 编号")
    manual.append(
        f"严格口径：包含度阈值 {threshold}；知识库记录只要与任一测试样本 ≥{threshold} "
        "即进入剔除清单，不再只取每个测试样本的最佳匹配"
    )
    if near_dup["pairs_ge_threshold"]:
        manual.append(
            f"近似片段：全量配对 {near_dup['pairs_ge_threshold']} 对"
            f"（≥0.8 {near_dup['pairs_ge_threshold_ge_0.8']} 对，"
            f"{threshold}~0.8 {near_dup['pairs_ge_threshold_0.5_to_0.8']} 对），"
            f"涉及 {near_dup['excluded_knowledge_count']} 条知识记录"
        )
    else:
        manual.append(
            f"近似片段：全量配对为 0，最高包含度 {near_dup['max_containment']} "
            f"低于阈值 {threshold}"
        )
    if cards_mode and near_dup["pairs_ge_threshold"]:
        manual.append(
            "卡片模式：以上命中是已入索引卡片的**残余**交集，说明剔除后仍有内容近似"
        )

    report: dict[str, Any] = {
        "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "mode": "cards" if cards_mode else "knowledge",
        "threshold": threshold,
        "excluded_knowledge_idxs": all_excluded,
        "exclusion": {
            "threshold_containment": threshold,
            "near_duplicate_count": len(code_excluded),
            "cve_text_mention_count": len(cve_excluded),
            "total_unique": len(all_excluded),
            "cve_text_idxs": cve_excluded,
        },
        "inputs": {
            "knowledge": {
                "name": knowledge_path.name,
                "rows": len(knowledge),
                "size_bytes": knowledge_path.stat().st_size,
                "role": "cards" if cards_mode else "knowledge",
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
    cards_mode = report.get("mode") == "cards"
    threshold = report.get("threshold", DEFAULT_THRESHOLD)
    exclusion = report.get("exclusion", {})
    title = (
        "# P3 索引卡片残余泄漏审计"
        if cards_mode
        else "# P3 知识库泄漏审计报告（严格口径）"
    )
    lines = [
        title,
        "",
        f"- 生成时间：{report['generated_at']}",
        f"- 审计对象：`{inputs['knowledge']['name']}`，{inputs['knowledge']['rows']} 行，"
        f"{inputs['knowledge']['size_bytes'] / 1024 / 1024:.1f} MB",
        f"- 测试集：`{inputs['test']['name']}`，{inputs['test']['rows']} 行",
        f"- 近似重复阈值：包含度 ≥{threshold}",
        f"- 审计耗时：{report['elapsed_seconds']}s",
        "",
        "## 结论",
        "",
        "以下维度**无交集**：",
        "",
    ]
    lines += [f"- {item}" for item in report["conclusion"]["no_overlap_dimensions"]] or [
        "- 无"
    ]
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
        f"- 比较：每条测试函数 vs 全部{'索引卡片' if cards_mode else '知识库'}函数，"
        f"单条最多截取 {near['cap_chars']} 个规范化字符",
        f"- 全量配对（≥{threshold}）：{near['pairs_ge_threshold']} 对"
        f"（≥0.80 {near['pairs_ge_threshold_ge_0.8']} 对，"
        f"{threshold}~0.80 {near['pairs_ge_threshold_0.5_to_0.8']} 对）",
        f"- 涉及{'索引卡片' if cards_mode else '知识记录'}："
        f"{near['excluded_knowledge_count']} 条",
        f"- 严格剔除清单：近似重复 {exclusion.get('near_duplicate_count', 0)} 条 + "
        f"CVE 文本提及 {exclusion.get('cve_text_mention_count', 0)} 条，"
        f"去重后 {exclusion.get('total_unique', 0)} 条",
        f"- 每测试样本最佳配对（旧 0.8 口径）：≥0.80 {near['pairs_ge_0.8']} 对，"
        f"0.50~0.80 {near['pairs_0.5_to_0.8']} 对，最高 {near['max_containment']}",
        "",
    ]

    if exclusion.get("cve_text_idxs"):
        lines += [
            "CVE 文本提及命中的知识记录（测试函数注释里出现同编号 CVE）："
            f"`{', '.join(str(idx) for idx in exclusion['cve_text_idxs'])}`",
            "",
        ]

    if near["mid_similarity_pairs"]:
        lines += [
            f"### 每测试样本最佳配对中 0.50~0.80 的 {len(near['mid_similarity_pairs'])} 对",
            "",
            "| 测试 idx | 知识 idx | 项目 | CVE | 包含度 |",
            "|---|---|---|---|---|",
        ]
        for item in near["mid_similarity_pairs"]:
            lines.append(
                f"| {item['test_idx']} | {item['knowledge_idx']} | "
                f"{item['knowledge_project']} | {item['knowledge_cve']} | "
                f"{item['containment']} |"
            )
        lines.append("")

    if near["pairs"]:
        lines += [
            "### 全量配对（按包含度降序，前 20 对）",
            "",
            "| 测试 idx | 知识 idx | 项目 | CVE | 包含度 | 同项目 | 测试文本含该 CVE |",
            "|---|---|---|---|---|---|---|",
        ]
        for item in near["pairs"][:20]:
            lines.append(
                f"| {item['test_idx']} | {item['knowledge_idx']} | "
                f"{item['knowledge_project']} | {item['knowledge_cve']} | "
                f"{item['containment']} | {'是' if item['same_project'] else '否'} | "
                f"{'是' if item['cve_mention_match'] else '否'} |"
            )
        lines.append("")

    if cards_mode:
        lines += [
            "## 复现",
            "",
            "```powershell",
            "python -m knowledge.audit_leakage --cards "
            "--knowledge cache\\knowledge\\cards.jsonl "
            f"--threshold {threshold}",
            "```",
            "",
        ]
    else:
        lines += [
            "## 复现",
            "",
            "```powershell",
            "python -m knowledge.audit_leakage --knowledge <knowledge.jsonl> "
            f"--test cache\\dataset\\test.jsonl --threshold {threshold}",
            "```",
            "",
        ]
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="知识库与测试集泄漏审计（严格口径）")
    parser.add_argument("--knowledge", type=Path, help="knowledge.jsonl 或 cards.jsonl")
    parser.add_argument(
        "--test", type=Path, default=REPO_ROOT / "cache" / "dataset" / "test.jsonl"
    )
    parser.add_argument(
        "--out", type=Path, default=REPO_ROOT / "cache" / "knowledge" / "audit"
    )
    parser.add_argument(
        "--threshold",
        type=float,
        default=DEFAULT_THRESHOLD,
        help="8-gram 包含度阈值，默认 0.5；≥阈值即视为污染",
    )
    parser.add_argument(
        "--cards",
        action="store_true",
        help="审计已入索引的 cards.jsonl，输出残余交集（leakage-audit-cards.*）",
    )
    args = parser.parse_args(argv)

    knowledge_path = args.knowledge
    if knowledge_path is None and args.cards:
        knowledge_path = REPO_ROOT / "cache" / "knowledge" / "cards.jsonl"
    if knowledge_path is None:
        knowledge_path = _knowledge_default()
    if knowledge_path is None:
        parser.error("必须用 --knowledge 指定文件，或设置 FUNCGUARD_KNOWLEDGE_PATH")
    if not Path(knowledge_path).exists():
        parser.error(f"文件不存在：{knowledge_path}")
    if not Path(args.test).exists():
        parser.error(f"测试集不存在：{args.test}")

    report = audit(
        Path(knowledge_path),
        Path(args.test),
        Path(args.out),
        threshold=args.threshold,
        cards_mode=args.cards,
    )
    args.out.mkdir(parents=True, exist_ok=True)
    stem = "leakage-audit-cards" if args.cards else "leakage-audit"
    json_path = args.out / f"{stem}.json"
    md_path = args.out / f"{stem}.md"
    json_path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    md_path.write_text(render_markdown(report) + "\n", encoding="utf-8")

    checks = report["checks"]
    near = checks["near_duplicate"]
    print(f"idx 交集：{checks['idx']['overlap_count']}")
    print(f"commit 交集：{checks['commit']['overlap_count']}")
    print(f"CVE 文本提及交集：{len(checks['cve_direct']['text_overlap'])}")
    print(f"规范化哈希交集：{checks['normalized_hash']['overlap_count']}")
    print(f"阈值：包含度 ≥{near['threshold']}")
    print(
        f"近似片段（全量）：{near['pairs_ge_threshold']} 对"
        f"（≥0.8 {near['pairs_ge_threshold_ge_0.8']}，"
        f"阈值~0.8 {near['pairs_ge_threshold_0.5_to_0.8']}），"
        f"涉及 {near['excluded_knowledge_count']} 条记录"
    )
    print(
        f"旧口径（每样本最佳）：≥0.8 {near['pairs_ge_0.8']} 对，"
        f"0.5~0.8 {near['pairs_0.5_to_0.8']} 对，最高 {near['max_containment']}"
    )
    exclusion = report.get("exclusion", {})
    print(
        f"严格剔除清单：近似重复 {exclusion.get('near_duplicate_count', 0)} 条 + "
        f"CVE 文本提及 {exclusion.get('cve_text_mention_count', 0)} 条，"
        f"去重后 {exclusion.get('total_unique', 0)} 条"
    )
    print(f"审计耗时：{report['elapsed_seconds']}s")
    print(f"报告：{json_path} / {md_path}")
    print(f"输出目录：{display_path(args.out)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
