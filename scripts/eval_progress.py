"""查看全量评测进度。

用法：
    python scripts/eval_progress.py
    python scripts/eval_progress.py --follow      # 每 10 秒刷新
    python scripts/eval_progress.py --cache D:/FuncGuardCache
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import time
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_DATASET = ROOT / "dataset" / "test.jsonl"


def _read_jsonl(path: Path) -> list[dict]:
    if not path.exists():
        return []
    rows = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return rows


def _repo_of(url: str) -> str:
    match = re.search(r"github\.com/([^/]+)/([^/]+)/commit/", url or "")
    if match:
        return f"{match.group(1)}__{match.group(2)}"
    if "git.qemu.org" in (url or ""):
        return "qemu__qemu"
    return "unknown"


def _dataset_rows(path: Path) -> list[dict]:
    if not path.exists():
        return []
    rows = []
    for line in path.read_text(encoding="utf-8-sig").splitlines():
        if line.strip():
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return rows


def _render(cache: Path, dataset: Path) -> str:
    out = cache / "eval"
    dataset_rows = _dataset_rows(dataset)
    total = len(dataset_rows)
    repo_totals = Counter(_repo_of(row.get("commit_url")) for row in dataset_rows)

    status_rows = _read_jsonl(out / "status.jsonl")
    status_map = {row.get("idx"): row for row in status_rows}
    status_counts = Counter(row.get("status") for row in status_rows)
    done = [row for row in status_rows if row.get("status") == "done"]
    done_repo = Counter(row.get("repo") for row in done)

    predictions = _read_jsonl(out / "predictions.jsonl")
    pred_counts = Counter(row.get("prediction") for row in predictions if "prediction" in row)

    failures: dict[str, Counter] = defaultdict(Counter)
    for row in status_rows:
        if row.get("status") != "done":
            failures[row.get("repo") or "unknown"][row.get("status")] += 1

    log_lines = []
    log_path = out / "run.log"
    if log_path.exists():
        log_lines = log_path.read_text(encoding="utf-8").splitlines()

    current = ""
    for line in reversed(log_lines):
        if re.search(r"\[\d+/\d+\] .*pending", line) or "prepare " in line or "pred=" in line:
            current = line
            break

    elapsed = ""
    if log_lines:
        first = log_lines[0][1:20] if log_lines[0].startswith("[") else ""
        try:
            start = datetime.fromisoformat(first)
            elapsed = f"{(datetime.now(start.tzinfo) - start).total_seconds() / 60:.1f} 分钟"
        except ValueError:
            pass

    lines = [
        f"缓存目录: {cache}",
        f"样本: {total}  已写入状态: {len(status_rows)}  已完成: {len(done)}  "
        f"预测数: {len(predictions)}  prediction 分布: {dict(pred_counts)}",
        f"状态: {dict(status_counts)}",
        f"已用时: {elapsed or '未知'}",
        f"最近事件: {current or '（尚无）'}",
    ]

    if failures:
        lines.append("未完成/失败:")
        for repo, counts in sorted(failures.items()):
            lines.append(
                f"  {repo}: {dict(counts)}  "
                f"(总样本 {repo_totals.get(repo, '?')}, 已完成 {done_repo.get(repo, 0)})"
            )

    lines.append("最近日志:")
    lines.extend("  " + line for line in log_lines[-10:])
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="查看 FuncGurad 全量评测进度")
    parser.add_argument(
        "--cache",
        type=Path,
        default=Path(r"D:/FuncGuardCache"),
    )
    parser.add_argument("--dataset", type=Path, default=DEFAULT_DATASET)
    parser.add_argument("--follow", action="store_true", help="每 10 秒刷新")
    parser.add_argument("--interval", type=float, default=10.0)
    args = parser.parse_args(argv)

    try:
        while True:
            if args.follow:
                sys.stdout.write("\033[2J\033[H")
            print(_render(args.cache, args.dataset), flush=True)
            if not args.follow:
                return 0
            time.sleep(max(1.0, args.interval))
    except KeyboardInterrupt:
        return 0


if __name__ == "__main__":
    raise SystemExit(main())