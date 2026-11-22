"""运行编排：加载样本与快照、驱动 Agent、写出 result.jsonl / report.json / trace.jsonl。

契约说明：
- `result.jsonl` 每行只写 idx 与 prediction，严格符合 ResultRecord；
- P-C / P-V / P-B / P-R 由官方相邻配对口径计算，写在 report.json；
- `trace.jsonl` 每行符合 TraceEvent 契约。
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any

from backend.agent.loop import run_agent
from backend.agent.trace import TraceWriter
from backend.config import AgentSettings
from backend.providers import create_provider
from backend.services.sample_service import (
    SampleServiceError,
    find_pair,
    load_dataset,
    load_sample_meta,
    resolve_snapshot_dir,
)
from bench.dataset import Sample
from repo_service.config import display_path
from repo_service.config import Settings as RepoServiceSettings
from repo_tools.base import SnapshotGuard, ToolLimits
from repo_tools.registry import tool_names

CATEGORY_BY_PREDICTION = {
    (1, 0): "P-C",
    (1, 1): "P-V",
    (0, 0): "P-B",
    (0, 1): "P-R",
}

CATEGORY_MEANING = {
    "P-C": "漏洞版报 1、修复版报 0，成功检出",
    "P-V": "两版都报 1，过度上报",
    "P-B": "两版都报 0，未检出",
    "P-R": "漏洞版报 0、修复版报 1，方向反转",
}


def _stamp() -> str:
    return datetime.now().strftime("%Y%m%d-%H%M%S")


def _collect_targets(
    requested_idx: list[int],
    samples: list[Sample],
    index: dict[int, Sample],
    pair_mode: bool,
) -> list[Sample]:
    targets: list[Sample] = []
    seen: set[int] = set()
    for idx in requested_idx:
        if idx not in index:
            raise SampleServiceError(f"idx={idx} 不在测试集中")
        if pair_mode:
            vulnerable, fixed = find_pair(samples, idx)
            candidates = [vulnerable, fixed]
        else:
            candidates = [index[idx]]
        for sample in candidates:
            if sample.idx not in seen:
                seen.add(sample.idx)
                targets.append(sample)
    return targets


def run(
    requested_idx: list[int],
    dataset_path: Path,
    settings: AgentSettings,
    output_dir: Path | None = None,
    pair_mode: bool = True,
) -> dict[str, Any]:
    samples, index = load_dataset(dataset_path)
    targets = _collect_targets(requested_idx, samples, index, pair_mode)
    provider, fallback_reason = create_provider(settings)
    repo_settings = RepoServiceSettings.from_env()

    if output_dir is None:
        suffix = "-".join(str(idx) for idx in requested_idx)
        output_dir = settings.runs_dir / f"{_stamp()}-idx-{suffix}"
    output_dir = output_dir.resolve()
    output_dir.mkdir(parents=True, exist_ok=True)

    trace_path = output_dir / "trace.jsonl"
    trace_path.write_text("", encoding="utf-8")

    limits = ToolLimits(
        max_file_bytes=settings.max_file_bytes,
        max_results=settings.max_results,
        max_read_lines=settings.max_read_lines,
        max_scan_files=settings.max_scan_files,
    )

    runs: list[dict[str, Any]] = []
    for sample in targets:
        meta = load_sample_meta(repo_settings, sample.idx)
        snapshot_dir = resolve_snapshot_dir(meta)
        guard = SnapshotGuard(snapshot_dir, limits)

        run_id = f"{_stamp()}-{sample.idx}"
        trace = TraceWriter(run_id, trace_path)
        outcome = run_agent(
            sample=sample,
            guard=guard,
            provider=provider,
            settings=settings,
            trace=trace,
            snapshot_display=display_path(snapshot_dir),
        )

        runs.append(
            {
                "idx": sample.idx,
                "target": sample.target,
                "run_id": outcome.run_id,
                "snapshot_dir": display_path(snapshot_dir),
                "method": meta.get("method"),
                "requested_commit": meta.get("requested_commit"),
                "resolved_commit": meta.get("resolved_commit"),
                "verdict": outcome.verdict,
                "prediction": outcome.prediction,
                "reason": outcome.reason,
                "evidence": outcome.evidence,
                "steps": outcome.steps,
                "llm_steps": outcome.llm_steps,
                "tool_calls": outcome.tool_calls,
                "timed_out": outcome.timed_out,
                "hit_max_steps": outcome.hit_max_steps,
                "evidence_gate": outcome.gate,
                "is_stub": outcome.is_stub,
            }
        )

    result_path = output_dir / "result.jsonl"
    with result_path.open("w", encoding="utf-8", newline="\n") as stream:
        for item in runs:
            stream.write(
                json.dumps(
                    {"idx": item["idx"], "prediction": item["prediction"]},
                    ensure_ascii=False,
                )
                + "\n"
            )

    predictions = {item["idx"]: item["prediction"] for item in runs}
    pair_info: dict[str, Any] | None = None
    for idx in requested_idx:
        vulnerable, fixed = find_pair(samples, idx)
        if vulnerable.idx in predictions and fixed.idx in predictions:
            key = (predictions[vulnerable.idx], predictions[fixed.idx])
            category = CATEGORY_BY_PREDICTION[key]
            pair_info = {
                "vulnerable_idx": vulnerable.idx,
                "fixed_idx": fixed.idx,
                "predictions": {
                    str(vulnerable.idx): predictions[vulnerable.idx],
                    str(fixed.idx): predictions[fixed.idx],
                },
                "category": category,
                "meaning": CATEGORY_MEANING[category],
                "penalty_rule": "P-R > P-C / 2 时等级下降一级",
            }
            break

    report: dict[str, Any] = {
        "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "provider": {
            "name": provider.name,
            "is_stub": provider.is_stub,
            "model": getattr(provider, "model", None),
            "base_url": getattr(provider, "base_url", None),
            "fallback_reason": fallback_reason,
            "tools": tool_names(),
        },
        "dataset": display_path(dataset_path),
        "pair": pair_info,
        "runs": runs,
        "contract_mapping": {
            "result_record": "result.jsonl 每行仅含 idx 与 prediction，符合 result_record.schema.json",
            "category": "P-C/P-V/P-B/P-R 按官方相邻配对口径由两版 prediction 计算，写入 report.pair.category",
            "trace_event": "trace.jsonl 每行符合 trace_event.schema.json",
        },
        "stub_notice": (
            "本次结果由确定性 stub provider 生成，仅用于验证端到端链路，"
            "不代表真实漏洞检测能力。"
            if provider.is_stub
            else None
        ),
    }
    report_path = output_dir / "report.json"
    report_path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )

    return {
        "output_dir": str(output_dir),
        "result_path": str(result_path),
        "report_path": str(report_path),
        "trace_path": str(trace_path),
        "provider": report["provider"],
        "runs": runs,
        "pair": pair_info,
    }
