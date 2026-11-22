"""加载并校验测试集，输出摘要。

用法（在仓库根目录执行）：
    python -m bench.load_dataset --dataset cache/dataset/test.jsonl
"""

from __future__ import annotations

import argparse
from pathlib import Path

from bench.dataset import load_samples


def default_dataset_path() -> Path:
    return Path("cache/dataset/test.jsonl")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="加载并校验测试集 JSONL")
    parser.add_argument(
        "--dataset",
        type=Path,
        default=default_dataset_path(),
        help="测试集路径（默认：cache/dataset/test.jsonl）",
    )
    return parser


def main() -> int:
    args = build_parser().parse_args()
    samples = load_samples(args.dataset)

    vulnerable = sum(1 for sample in samples if sample.target == 1)
    fixed = len(samples) - vulnerable

    print(f"文件: {args.dataset}")
    print(f"记录数: {len(samples)}")
    print(f"唯一 idx: {len({sample.idx for sample in samples})}")
    print(f"配对数: {len(samples) // 2}")
    print(f"target=1（漏洞版）: {vulnerable}")
    print(f"target=0（修复版）: {fixed}")
    print("校验通过: 是")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
