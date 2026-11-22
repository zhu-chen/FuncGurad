"""按固定策略生成合法的 result.jsonl（只包含 idx 与 prediction）。

用法（在仓库根目录执行）：
    python -m bench.write_result --strategy all0 --output cache/baseline/all0.jsonl
    python -m bench.write_result --strategy all1 --output cache/baseline/all1.jsonl
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from bench.dataset import load_samples

STRATEGIES = {
    "all0": 0,
    "all1": 1,
}


def default_dataset_path() -> Path:
    return Path("cache/dataset/test.jsonl")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="生成 result.jsonl 基线")
    parser.add_argument(
        "--dataset",
        type=Path,
        default=default_dataset_path(),
        help="测试集路径（默认：cache/dataset/test.jsonl）",
    )
    parser.add_argument(
        "--strategy",
        choices=sorted(STRATEGIES),
        required=True,
        help="预测策略：all0 全判无漏洞，all1 全判有漏洞",
    )
    parser.add_argument(
        "--output",
        type=Path,
        required=True,
        help="输出 result.jsonl 路径",
    )
    return parser


def main() -> int:
    args = build_parser().parse_args()
    samples = load_samples(args.dataset)
    prediction = STRATEGIES[args.strategy]

    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("w", encoding="utf-8", newline="\n") as stream:
        for sample in samples:
            record = {"idx": sample.idx, "prediction": prediction}
            stream.write(json.dumps(record, ensure_ascii=False) + "\n")

    print(f"已写出: {args.output}")
    print(f"策略: {args.strategy}（prediction={prediction}）")
    print(f"记录数: {len(samples)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
