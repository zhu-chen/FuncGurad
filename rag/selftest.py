"""检索效果自测：已知案例做 query，同项目不同 CVE 做负例。

口径：
- 正例：取知识库中 cve_desc 足够长且带 CVE 编号的卡片，用描述前 300 字检索，
         看 top-3 是否命中同一 CVE（strict: 是否命中同一 source_idx）。
- 负例：同项目、不同 CVE 的描述做 query，看 top-3 是否错误命中目标 CVE。
结果写 cache/knowledge/selftest/。
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
import time
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any

from rag.retriever import KnowledgeRetriever, default_index_dir
from repo_service.config import REPO_ROOT


def pick_cases(cards: list[dict[str, Any]], limit: int) -> list[dict[str, Any]]:
    """按 idx 排序后等距抽样，尽量覆盖更多项目。"""
    eligible = [
        card
        for card in cards
        if card.get("cve") and len(card.get("cve_desc") or "") >= 80
    ]
    eligible.sort(key=lambda item: item["source_idx"])
    if len(eligible) <= limit:
        return eligible
    step = len(eligible) / limit
    return [eligible[int(index * step)] for index in range(limit)]


CASE_FIELDS = (
    "source_idx",
    "ref",
    "cve",
    "project",
    "cve_desc",
    "commit_message",
    "func_signature",
)


def load_cases_file(path: Path) -> list[dict[str, Any]]:
    """加载固定案例文件（JSON 列表），用于收紧前后同一批 query 复测。"""
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(payload, list):
        raise SystemExit(f"案例文件格式错误，需要 JSON 列表：{path}")
    cases: list[dict[str, Any]] = []
    for item in payload:
        if not isinstance(item, dict) or "source_idx" not in item:
            continue
        cases.append({key: item.get(key) for key in CASE_FIELDS})
    if not cases:
        raise SystemExit(f"案例文件为空或缺少 source_idx：{path}")
    cases.sort(key=lambda item: item["source_idx"])
    return cases


def pick_cases_by_idx(
    cards: list[dict[str, Any]], case_idxs: list[int]
) -> list[dict[str, Any]]:
    """按给定 idx 取案例；索引中已不存在的 idx 会被跳过（另行报告）。"""
    wanted = set(case_idxs)
    cases = [card for card in cards if card["source_idx"] in wanted]
    cases.sort(key=lambda item: item["source_idx"])
    return cases


def pick_negative(
    cards: list[dict[str, Any]], target: dict[str, Any], used: set[int]
) -> dict[str, Any] | None:
    """同项目、不同 CVE、描述足够长且未被用过的卡片。"""
    for card in cards:
        if card["source_idx"] in used:
            continue
        if card.get("project") != target.get("project"):
            continue
        if not card.get("cve") or card["cve"] == target.get("cve"):
            continue
        if len(card.get("cve_desc") or "") < 80:
            continue
        return card
    return None


def run_selftest(
    index_dir: Path,
    sample_size: int,
    top_k: int,
    case_idxs: list[int] | None = None,
    cases_file: Path | None = None,
) -> dict[str, Any]:
    retriever = KnowledgeRetriever(index_dir)
    cards = retriever.cards
    cards_source = "index"
    if cases_file is not None:
        cases = load_cases_file(cases_file)
        cards_source = f"file:{Path(cases_file).name}"
    elif case_idxs:
        cases = pick_cases_by_idx(cards, case_idxs)
        cards_source = "case_idxs"
    else:
        cases = pick_cases(cards, sample_size)
    if not cases:
        raise SystemExit("知识库中没有可用于自测的卡片（需要 cve 与非空 cve_desc）")

    index_idxs = {card["source_idx"] for card in cards}
    missing_cases = [
        case["source_idx"] for case in cases if case["source_idx"] not in index_idxs
    ]

    used = {card["source_idx"] for card in cases}
    positive_hits = 0
    strict_hits = 0
    negative_false_hits = 0
    negative_total = 0
    latencies: list[float] = []
    failures: list[dict[str, Any]] = []
    negative_failures: list[dict[str, Any]] = []

    per_project: dict[str, list[int]] = defaultdict(lambda: [0, 0])

    extra = {
        "commit_message": {"cases": 0, "hits": 0, "failures": []},
        "signature": {"cases": 0, "hits": 0, "failures": []},
    }

    for card in cases:
        query = card["cve_desc"][:300]
        started = time.perf_counter()
        result = retriever.search(query, top_k=top_k)
        latencies.append((time.perf_counter() - started) * 1000)
        hits = result["hits"]
        hit_cves = [hit.get("cve") for hit in hits]
        hit_refs = [hit.get("ref") for hit in hits]

        project_stats = per_project[card.get("project") or "unknown"]
        project_stats[1] += 1
        if card["cve"] in hit_cves:
            positive_hits += 1
            project_stats[0] += 1
        else:
            failures.append(
                {
                    "query_cve": card["cve"],
                    "project": card.get("project"),
                    "query": query[:120],
                    "top_hits": hit_cves,
                }
            )
        if card["ref"] in hit_refs:
            strict_hits += 1

        for probe, field, limit in (
            ("commit_message", "commit_message", 200),
            ("signature", "func_signature", 160),
        ):
            text = (card.get(field) or "").strip()
            if len(text) < 20:
                continue
            probe_result = retriever.search(text[:limit], top_k=top_k)
            probe_cves = [hit.get("cve") for hit in probe_result["hits"]]
            extra[probe]["cases"] += 1
            if card["cve"] in probe_cves:
                extra[probe]["hits"] += 1
            else:
                extra[probe]["failures"].append(
                    {
                        "query_cve": card["cve"],
                        "project": card.get("project"),
                        "query": text[:120],
                        "top_hits": probe_cves,
                    }
                )

        negative = pick_negative(cards, card, used)
        if negative is None:
            continue
        negative_total += 1
        neg_query = negative["cve_desc"][:300]
        started = time.perf_counter()
        neg_result = retriever.search(neg_query, top_k=top_k)
        latencies.append((time.perf_counter() - started) * 1000)
        neg_cves = [hit.get("cve") for hit in neg_result["hits"]]
        if card["cve"] in neg_cves:
            negative_false_hits += 1
            negative_failures.append(
                {
                    "target_cve": card["cve"],
                    "negative_query_cve": negative["cve"],
                    "project": card.get("project"),
                    "top_hits": neg_cves,
                }
            )

    def rate(numerator: int, denominator: int) -> float:
        return round(numerator / denominator, 4) if denominator else 0.0

    latencies_sorted = sorted(latencies)
    p95_index = max(0, int(len(latencies_sorted) * 0.95) - 1)

    return {
        "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "index_dir": str(index_dir),
        "retriever": "bm25",
        "top_k": top_k,
        "cases_source": cards_source,
        "cases_missing_from_index": missing_cases,
        "positive": {
            "cases": len(cases),
            "cve_top3_hits": positive_hits,
            "cve_top3_rate": rate(positive_hits, len(cases)),
            "exact_ref_top3_hits": strict_hits,
            "exact_ref_top3_rate": rate(strict_hits, len(cases)),
            "failures": failures[:10],
        },
        "negative": {
            "cases": negative_total,
            "false_hits": negative_false_hits,
            "false_hit_rate": rate(negative_false_hits, negative_total),
            "failures": negative_failures[:10],
        },
        "extra_probes": {
            probe: {
                "cases": values["cases"],
                "hits": values["hits"],
                "rate": rate(values["hits"], values["cases"]),
                "failures": values["failures"][:5],
            }
            for probe, values in extra.items()
        },
        "per_project": {
            project: {"hits": values[0], "cases": values[1]}
            for project, values in sorted(per_project.items())
        },
        "latency_ms": {
            "samples": len(latencies),
            "median": round(statistics.median(latencies), 2) if latencies else 0.0,
            "p95": round(latencies_sorted[p95_index], 2) if latencies else 0.0,
            "max": round(max(latencies), 2) if latencies else 0.0,
        },
    }


def render_markdown(report: dict[str, Any]) -> str:
    positive = report["positive"]
    negative = report["negative"]
    latency = report["latency_ms"]
    lines = [
        "# P3 检索效果自测",
        "",
        f"- 生成时间：{report['generated_at']}",
        f"- 索引：`{report['index_dir']}`（{report['retriever']}）",
        f"- 案例来源：{report.get('cases_source', 'index')}；"
        f"已从索引剔除的案例：{len(report.get('cases_missing_from_index') or [])} 条",
        f"- 正例：{positive['cases']} 条；负例：{negative['cases']} 条；top_k={report['top_k']}",
        "",
        "## 指标",
        "",
        "| 指标 | 数值 |",
        "|---|---|",
        f"| 正例 top-3 命中同一 CVE（CVE 描述 query） | {positive['cve_top3_hits']}/{positive['cases']} "
        f"= {positive['cve_top3_rate']:.1%} |",
        f"| 正例 top-3 命中同一记录 | {positive['exact_ref_top3_hits']}/{positive['cases']} "
        f"= {positive['exact_ref_top3_rate']:.1%} |",
        f"| 负例误命中目标 CVE | {negative['false_hits']}/{negative['cases']} "
        f"= {negative['false_hit_rate']:.1%} |",
        f"| 单次检索中位数 / P95 | {latency['median']} ms / {latency['p95']} ms |",
        "",
    ]
    probes = report["extra_probes"]
    lines += [
        "## 额外探针",
        "",
        "| query 来源 | top-3 命中同一 CVE |",
        "|---|---|",
        f"| 修复提交标题（commit_message） | {probes['commit_message']['hits']}/"
        f"{probes['commit_message']['cases']} = {probes['commit_message']['rate']:.1%} |",
        f"| 函数签名（func_signature） | {probes['signature']['hits']}/"
        f"{probes['signature']['cases']} = {probes['signature']['rate']:.1%} |",
        "",
        "## 失败样例",
        "",
    ]
    if positive["failures"]:
        lines.append("正例未命中（query 的 CVE 不在 top-3）：")
        lines.append("")
        for item in positive["failures"][:5]:
            lines.append(
                f"- {item['project']} {item['query_cve']} → {item['top_hits']}"
                f"（query: {item['query']}）"
            )
    else:
        lines.append("正例全部命中。")
    lines.append("")
    if negative["failures"]:
        lines.append("负例误命中（同项目不同 CVE 被召回）：")
        lines.append("")
        for item in negative["failures"][:5]:
            lines.append(
                f"- 目标 {item['target_cve']} / 负例 query {item['negative_query_cve']}"
                f" → {item['top_hits']}"
            )
    else:
        lines.append("负例无误命中。")
    lines.append("")
    for probe_name, label in (
        ("commit_message", "修复提交标题"),
        ("signature", "函数签名"),
    ):
        probe = probes[probe_name]
        if not probe["failures"]:
            continue
        lines.append(f"{label} query 未命中（{probe['hits']}/{probe['cases']} 命中）：")
        lines.append("")
        for item in probe["failures"][:3]:
            lines.append(
                f"- {item['project']} {item['query_cve']} → {item['top_hits']}"
                f"（query: {item['query']}）"
            )
        lines.append("")
    lines.extend(
        [
            "",
            "## 说明",
            "",
            "- 正例 query 使用 NVD 漏洞描述前 300 字，不代表真实用户提问分布。",
            "- 负例取同项目、不同 CVE 的描述，检验 BM25 是否只按项目词面召回。",
            "- 以上数字来自单次确定性运行，索引不变则结果可复现。",
            "",
        ]
    )
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="知识库检索自测")
    parser.add_argument("--index-dir", type=Path, default=default_index_dir())
    parser.add_argument("--samples", type=int, default=40)
    parser.add_argument("--top-k", type=int, default=3)
    parser.add_argument(
        "--case-idxs",
        default="",
        help="固定案例 source_idx，逗号分隔；用于收紧前后同一批 query 对比",
    )
    parser.add_argument(
        "--cases-file",
        type=Path,
        default=None,
        help="固定案例 JSON 文件（含 source_idx/cve/project/cve_desc 等字段）",
    )
    parser.add_argument(
        "--out", type=Path, default=REPO_ROOT / "cache" / "knowledge" / "selftest"
    )
    args = parser.parse_args(argv)

    case_idxs = [
        int(part.strip()) for part in str(args.case_idxs).split(",") if part.strip()
    ]
    report = run_selftest(
        Path(args.index_dir),
        args.samples,
        args.top_k,
        case_idxs=case_idxs or None,
        cases_file=args.cases_file,
    )
    args.out.mkdir(parents=True, exist_ok=True)
    json_path = args.out / "selftest.json"
    md_path = args.out / "selftest.md"
    json_path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    md_path.write_text(render_markdown(report) + "\n", encoding="utf-8")

    positive = report["positive"]
    negative = report["negative"]
    latency = report["latency_ms"]
    print(
        f"正例 top-3 命中 CVE：{positive['cve_top3_hits']}/{positive['cases']} "
        f"({positive['cve_top3_rate']:.1%})；同记录命中 "
        f"{positive['exact_ref_top3_hits']}/{positive['cases']}"
    )
    print(
        f"负例误命中：{negative['false_hits']}/{negative['cases']} "
        f"({negative['false_hit_rate']:.1%})"
    )
    print(
        f"单次检索：中位数 {latency['median']}ms / P95 {latency['p95']}ms / "
        f"最大 {latency['max']}ms"
    )
    missing = report.get("cases_missing_from_index") or []
    if missing:
        print(f"已从索引剔除的案例：{len(missing)} 条 → {missing[:10]}")
    probes = report["extra_probes"]
    print(
        f"额外探针：commit_message {probes['commit_message']['hits']}/"
        f"{probes['commit_message']['cases']}，signature "
        f"{probes['signature']['hits']}/{probes['signature']['cases']}"
    )
    print(f"报告：{json_path} / {md_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
