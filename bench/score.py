"""按官方 evaluation.py 口径统计 P-C / P-V / P-B / P-R，并计算等级。

用法（在仓库根目录执行）：
    python -m bench.score --result cache/baseline/all0.jsonl --json-out cache/baseline/all0.report.json
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from bench.dataset import DatasetError, iter_pairs, load_samples, read_jsonl

CATEGORY_BY_PREDICTION = {
    (1, 0): "P-C",
    (1, 1): "P-V",
    (0, 0): "P-B",
    (0, 1): "P-R",
}

PENALTY_RULE = "P-R > P-C / 2 时等级下降一级（最低 D）"
DOWNGRADE = {"A": "B", "B": "C", "C": "D", "D": "D"}


def default_dataset_path() -> Path:
    return Path("cache/dataset/test.jsonl")


def load_predictions(path: Path) -> dict[int, int]:
    """按 idx 索引预测；相同预测可重复，冲突预测直接报错。"""
    predictions: dict[int, int] = {}

    for location, record in read_jsonl(path):
        if "idx" not in record:
            raise DatasetError(f"{location}: 缺少字段 idx")
        if "prediction" not in record:
            raise DatasetError(f"{location}: 缺少字段 prediction")

        idx = record["idx"]
        prediction = record["prediction"]
        if type(idx) is not int:
            raise DatasetError(f"{location}: idx 必须是整数")
        if type(prediction) is not int:
            raise DatasetError(f"{location}: prediction 必须是整数")
        if prediction not in (0, 1):
            raise DatasetError(f"{location}: prediction 必须为 0 或 1")

        if idx in predictions and predictions[idx] != prediction:
            raise DatasetError(f"{location}: idx={idx} 的重复 prediction 相互冲突")
        predictions[idx] = prediction

    return predictions


def base_grade(p_c: int) -> str:
    """仅按 P-C 数量得到基础等级。"""
    if p_c >= 30:
        return "A"
    if p_c >= 25:
        return "B"
    if p_c >= 20:
        return "C"
    return "D"


def evaluate(dataset_path: Path, result_path: Path) -> dict[str, Any]:
    """返回符合 EvalReport 契约的统计结果。"""
    samples = load_samples(dataset_path)
    predictions = load_predictions(result_path)

    expected_ids = {sample.idx for sample in samples}
    unknown = predictions.keys() - expected_ids
    if unknown:
        preview = ", ".join(map(str, sorted(unknown)[:10]))
        suffix = ", ..." if len(unknown) > 10 else ""
        raise DatasetError(f"结果包含 {len(unknown)} 个未知 idx：{preview}{suffix}")

    counts = {name: 0 for name in CATEGORY_BY_PREDICTION.values()}
    pairs_evaluated = 0

    for vulnerable, fixed in iter_pairs(samples):
        if vulnerable.idx not in predictions or fixed.idx not in predictions:
            continue
        predicted_pair = (predictions[vulnerable.idx], predictions[fixed.idx])
        counts[CATEGORY_BY_PREDICTION[predicted_pair]] += 1
        pairs_evaluated += 1

    grade = base_grade(counts["P-C"])
    penalty_applied = counts["P-R"] * 2 > counts["P-C"]
    final_grade = DOWNGRADE[grade] if penalty_applied else grade

    return {
        "total_samples": len(samples),
        "unique_idx": len(expected_ids),
        "pairs_total": len(samples) // 2,
        "pairs_evaluated": pairs_evaluated,
        "counts": counts,
        "base_grade": grade,
        "final_grade": final_grade,
        "penalty_applied": penalty_applied,
        "penalty_rule": PENALTY_RULE,
    }


def print_report(report: dict[str, Any]) -> None:
    counts = report["counts"]
    print(f"样本数: {report['total_samples']}")
    print(f"唯一 idx: {report['unique_idx']}")
    print(f"配对数: {report['pairs_total']}")
    print(f"已评测配对: {report['pairs_evaluated']}")
    print(f"P-C: {counts['P-C']}")
    print(f"P-V: {counts['P-V']}")
    print(f"P-B: {counts['P-B']}")
    print(f"P-R: {counts['P-R']}")
    print(f"基础等级: {report['base_grade']}")
    print(f"最终等级: {report['final_grade']}")
    print(f"是否降级: {'是' if report['penalty_applied'] else '否'}")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="统计 P-C/P-V/P-B/P-R 并计算等级")
    parser.add_argument(
        "--dataset",
        type=Path,
        default=default_dataset_path(),
        help="测试集路径（默认：cache/dataset/test.jsonl）",
    )
    parser.add_argument(
        "--result",
        type=Path,
        required=True,
        help="待评测的 result.jsonl",
    )
    parser.add_argument(
        "--json-out",
        type=Path,
        default=None,
        help="可选：把 EvalReport 写入指定 JSON 文件",
    )
    return parser


def main() -> int:
    args = build_parser().parse_args()
    report = evaluate(args.dataset, args.result)
    print_report(report)

    if args.json_out is not None:
        args.json_out.parent.mkdir(parents=True, exist_ok=True)
        with args.json_out.open("w", encoding="utf-8", newline="\n") as stream:
            json.dump(report, stream, ensure_ascii=False, indent=2)
            stream.write("\n")
        print(f"报告已写出: {args.json_out}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
