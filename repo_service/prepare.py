"""批量准备仓库快照与函数定位，逐条输出并写入 manifest。

用法（在仓库根目录执行）：
    # 只做 URL 分类，不下载，用于验证 300 条批处理能连续输出
    python -m repo_service.prepare --plan-only

    # 实际准备指定样本
    python -m repo_service.prepare --only-idx 194994 --only-idx 218852 --workers 2

    # 跑前 N 条
    python -m repo_service.prepare --limit 10
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

from bench.dataset import Sample, load_samples
from repo_service.config import Settings, display_path
from repo_service.locator import locate_function
from repo_service.snapshot import SnapshotManager
from repo_service.urls import classify_commit_url


def _base_record(sample: Sample) -> dict:
    return {
        "idx": sample.idx,
        "target": sample.target,
        "commit_url": sample.commit_url,
    }


def plan_sample(sample: Sample) -> dict:
    record = _base_record(sample)
    try:
        ref = classify_commit_url(sample.commit_url)
    except ValueError as exc:
        record.update({"status": "failed", "error": str(exc)})
        return record

    record.update(
        {
            "status": "planned" if ref.is_github else "manual_pending",
            "provider": ref.provider,
            "repo": ref.repo_key,
            "requested_commit": ref.commit,
            "reason": ref.reason,
        }
    )
    return record


def process_sample(
    sample: Sample,
    manager: SnapshotManager,
    settings: Settings,
    do_locate: bool,
) -> dict:
    started = time.time()
    record = _base_record(sample)
    try:
        outcome = manager.ensure_snapshot(sample)
    except Exception as exc:  # 兜底，保证批处理不中断
        record.update({"status": "failed", "error": str(exc)})
        return record

    record.update(
        {
            "status": outcome.status,
            "provider": outcome.ref.provider if outcome.ref else None,
            "repo": outcome.ref.repo_key if outcome.ref else None,
            "requested_commit": outcome.requested_commit,
            "resolved_commit": outcome.resolved_commit,
            "version_role": outcome.version_role,
            "method": outcome.method,
            "note": outcome.note,
            "snapshot_dir": (
                display_path(outcome.snapshot_dir) if outcome.snapshot_dir else None
            ),
            "reason": outcome.reason,
            "error": outcome.error,
        }
    )

    if outcome.status == "snapshot_ok" and do_locate and outcome.snapshot_dir:
        try:
            located = locate_function(
                outcome.snapshot_dir,
                sample.func,
                settings.max_source_file_bytes,
            )
            record.update(
                {
                    "locate_status": located.status,
                    "locate_path": located.path,
                    "locate_confidence": located.confidence,
                    "locate_method": located.method,
                    "locate_scanned_files": located.scanned_files,
                    "locate_elapsed_seconds": located.elapsed_seconds,
                }
            )
        except Exception as exc:
            record.update({"locate_status": "error", "locate_error": str(exc)})

    record["elapsed_seconds"] = round(time.time() - started, 2)
    return record


def format_line(index: int, total: int, record: dict) -> str:
    parts = [
        f"[{index:>3}/{total}]",
        f"idx={record['idx']}",
        f"target={record['target']}",
        f"status={record['status']}",
    ]
    if record.get("repo"):
        parts.append(f"repo={record['repo']}")
    if record.get("resolved_commit"):
        parts.append(f"resolved={record['resolved_commit'][:12]}")
    if record.get("method"):
        parts.append(f"method={record['method']}")
    if record.get("locate_status"):
        parts.append(f"locate={record['locate_status']}")
    if record.get("error"):
        parts.append(f"error={str(record['error']).splitlines()[0][:160]}")
    if record.get("reason"):
        parts.append(f"reason={record['reason']}")
    return " ".join(parts)


def build_parser(settings: Settings) -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="批量准备仓库快照并定位函数")
    parser.add_argument(
        "--dataset",
        type=Path,
        default=settings.default_dataset,
        help="测试集路径（默认：cache/dataset/test.jsonl）",
    )
    parser.add_argument(
        "--only-idx",
        type=int,
        action="append",
        default=None,
        help="只处理指定 idx，可重复",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=0,
        help="最多处理前 N 条（0 表示全部）",
    )
    parser.add_argument(
        "--workers",
        type=int,
        default=settings.max_workers,
        help="并发数（默认取 FUNCGUARD_MAX_WORKERS）",
    )
    parser.add_argument(
        "--plan-only",
        action="store_true",
        help="只分类 URL 并输出计划，不下载仓库",
    )
    parser.add_argument(
        "--no-locate",
        action="store_true",
        help="只准备快照，不做函数定位",
    )
    parser.add_argument(
        "--manifest",
        type=Path,
        default=settings.manifest_path,
        help="manifest 输出路径",
    )
    return parser


def main() -> int:
    settings = Settings.from_env()
    args = build_parser(settings).parse_args()
    settings.ensure_dirs()

    samples = load_samples(args.dataset)
    if args.only_idx:
        wanted = set(args.only_idx)
        samples = [sample for sample in samples if sample.idx in wanted]
    if args.limit > 0:
        samples = samples[: args.limit]
    if not samples:
        print("没有匹配的样本", file=sys.stderr)
        return 1

    args.manifest.parent.mkdir(parents=True, exist_ok=True)
    manager = SnapshotManager(settings)
    workers = max(1, args.workers)
    total = len(samples)
    started = time.time()

    status_counts: Counter = Counter()
    locate_counts: Counter = Counter()
    method_counts: Counter = Counter()

    with args.manifest.open("w", encoding="utf-8", newline="\n") as manifest:
        with ThreadPoolExecutor(max_workers=workers) as pool:
            if args.plan_only:
                futures = [pool.submit(plan_sample, sample) for sample in samples]
            else:
                futures = [
                    pool.submit(
                        process_sample,
                        sample,
                        manager,
                        settings,
                        not args.no_locate,
                    )
                    for sample in samples
                ]

            for index, future in enumerate(as_completed(futures), 1):
                record = future.result()
                status_counts[record["status"]] += 1
                locate_counts[record.get("locate_status") or "skipped"] += 1
                if record.get("method"):
                    method_counts[record["method"]] += 1
                print(format_line(index, total, record), flush=True)
                manifest.write(json.dumps(record, ensure_ascii=False) + "\n")
                manifest.flush()

    elapsed = round(time.time() - started, 2)
    print("=== summary ===")
    print("总样本:", total)
    print("状态:", dict(status_counts))
    print("快照方式:", dict(method_counts))
    print("定位:", dict(locate_counts))
    print("耗时:", f"{elapsed}s")
    print("manifest:", args.manifest)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
