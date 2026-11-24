"""底线：P-C/P-V/P-B/P-R 计数、A/B/C/D 阈值与 P-R 降级规则必须与课程口径一致。

依据：`bench/score.py` 的实现与其 docstring；仓库当前没有课程官方 `dataset/evaluation.py`，
因此预期值按 `bench/score.py` 与 README/交接文档记录的口径推导（A>=30、B>=25、C>=20、
否则 D；P-R*2 > P-C 时降一级）。若后续放入官方 evaluation.py，需重新复核本文件阈值。
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from bench.dataset import DatasetError
from bench.score import evaluate, load_predictions

PREDICTION_BY_CATEGORY = {
    "P-C": (1, 0),
    "P-V": (1, 1),
    "P-B": (0, 0),
    "P-R": (0, 1),
}


def _write_jsonl(path: Path, records: list[dict]) -> Path:
    path.write_text(
        "".join(json.dumps(record, ensure_ascii=False) + "\n" for record in records),
        encoding="utf-8",
    )
    return path


def _sample(idx: int, target: int) -> dict:
    return {
        "idx": idx,
        "commit_url": f"https://github.com/example/repo/commit/{idx:040x}",
        "target": target,
        "func": f"int func_{idx}(void) {{ return {target}; }}",
    }


def _build_case(tmp_path: Path, categories: list[str], fixed_first: bool = False):
    tmp_path.mkdir(parents=True, exist_ok=True)
    samples: list[dict] = []
    predictions: list[dict] = []
    for offset, category in enumerate(categories):
        vuln_idx = offset * 2
        fixed_idx = offset * 2 + 1
        records = [_sample(vuln_idx, 1), _sample(fixed_idx, 0)]
        if fixed_first:
            records.reverse()
        samples.extend(records)
        pred_vuln, pred_fixed = PREDICTION_BY_CATEGORY[category]
        predictions.append({"idx": vuln_idx, "prediction": pred_vuln})
        predictions.append({"idx": fixed_idx, "prediction": pred_fixed})

    dataset = _write_jsonl(tmp_path / "dataset.jsonl", samples)
    result = _write_jsonl(tmp_path / "result.jsonl", predictions)
    return dataset, result


def test_all_correct_thirty_pairs_gets_grade_a(tmp_path):
    dataset, result = _build_case(tmp_path, ["P-C"] * 30)

    report = evaluate(dataset, result)

    assert report["counts"] == {"P-C": 30, "P-V": 0, "P-B": 0, "P-R": 0}
    assert report["pairs_total"] == 30
    assert report["pairs_evaluated"] == 30
    assert report["base_grade"] == "A"
    assert report["final_grade"] == "A"
    assert report["penalty_applied"] is False


@pytest.mark.parametrize(
    ("correct", "expected_grade"),
    [(19, "D"), (20, "C"), (24, "C"), (25, "B"), (29, "B"), (30, "A")],
)
def test_grade_thresholds(tmp_path, correct, expected_grade):
    dataset, result = _build_case(tmp_path, ["P-C"] * correct)

    report = evaluate(dataset, result)

    assert report["base_grade"] == expected_grade
    assert report["final_grade"] == expected_grade


def test_all_reversed_is_penalized_and_stays_d(tmp_path):
    dataset, result = _build_case(tmp_path, ["P-R"] * 3)

    report = evaluate(dataset, result)

    assert report["counts"] == {"P-C": 0, "P-V": 0, "P-B": 0, "P-R": 3}
    assert report["base_grade"] == "D"
    assert report["final_grade"] == "D"
    assert report["penalty_applied"] is True


def test_all_benign_and_all_vulnerable_counts(tmp_path):
    benign_dataset, benign_result = _build_case(tmp_path, ["P-B"] * 4)
    benign = evaluate(benign_dataset, benign_result)
    assert benign["counts"] == {"P-C": 0, "P-V": 0, "P-B": 4, "P-R": 0}
    assert benign["final_grade"] == "D"

    vuln_dataset, vuln_result = _build_case(tmp_path, ["P-V"] * 4)
    vuln = evaluate(vuln_dataset, vuln_result)
    assert vuln["counts"] == {"P-C": 0, "P-V": 4, "P-B": 0, "P-R": 0}
    assert vuln["final_grade"] == "D"


def test_penalty_boundary_is_strict_greater_than_half(tmp_path):
    # P-C=2, P-R=1：2*1 > 2 不成立，不降级。
    ok_dataset, ok_result = _build_case(tmp_path / "ok", ["P-C"] * 2 + ["P-R"] * 1)
    ok = evaluate(ok_dataset, ok_result)
    assert (ok["counts"]["P-C"], ok["counts"]["P-R"]) == (2, 1)
    assert ok["penalty_applied"] is False

    # P-C=2, P-R=2：2*2 > 2 成立，降一级。
    bad_dir = tmp_path / "bad"
    bad_dir.mkdir()
    bad_dataset, bad_result = _build_case(bad_dir, ["P-C"] * 2 + ["P-R"] * 2)
    bad = evaluate(bad_dataset, bad_result)
    assert bad["penalty_applied"] is True
    assert (bad["base_grade"], bad["final_grade"]) == ("D", "D")


def test_downgrade_from_b_to_c_and_c_to_d(tmp_path):
    # 25 对 P-C + 13 对 P-R：25*... P-R*2=26 > 25，B 降 C。
    b_dir = tmp_path / "b"
    b_dir.mkdir()
    dataset, result = _build_case(b_dir, ["P-C"] * 25 + ["P-R"] * 13)
    report = evaluate(dataset, result)
    assert (report["base_grade"], report["final_grade"]) == ("B", "C")

    # 20 对 P-C + 11 对 P-R：22 > 20，C 降 D。
    c_dir = tmp_path / "c"
    c_dir.mkdir()
    dataset, result = _build_case(c_dir, ["P-C"] * 20 + ["P-R"] * 11)
    report = evaluate(dataset, result)
    assert (report["base_grade"], report["final_grade"]) == ("C", "D")


def test_mixed_counts_are_classified_per_pair(tmp_path):
    dataset, result = _build_case(
        tmp_path, ["P-C", "P-C", "P-V", "P-B", "P-R"]
    )

    report = evaluate(dataset, result)

    assert report["counts"] == {"P-C": 2, "P-V": 1, "P-B": 1, "P-R": 1}
    assert report["pairs_evaluated"] == 5


def test_pair_identity_follows_target_not_order(tmp_path):
    dataset, result = _build_case(tmp_path, ["P-C"] * 2, fixed_first=True)

    report = evaluate(dataset, result)

    assert report["counts"]["P-C"] == 2


def test_missing_predictions_skip_whole_pair(tmp_path):
    samples = [
        _sample(0, 1),
        _sample(1, 0),
        _sample(2, 1),
        _sample(3, 0),
    ]
    dataset = _write_jsonl(tmp_path / "dataset.jsonl", samples)
    # 只给第一对的两条预测，第二对整对缺失。
    result = _write_jsonl(
        tmp_path / "result.jsonl",
        [{"idx": 0, "prediction": 1}, {"idx": 1, "prediction": 0}],
    )

    report = evaluate(dataset, result)

    assert report["pairs_total"] == 2
    assert report["pairs_evaluated"] == 1
    assert report["counts"] == {"P-C": 1, "P-V": 0, "P-B": 0, "P-R": 0}


def test_duplicate_identical_prediction_allowed(tmp_path):
    dataset, result = _build_case(tmp_path, ["P-C"])
    records = [json.loads(line) for line in result.read_text(encoding="utf-8").splitlines()]
    _write_jsonl(result, records + [records[0]])

    report = evaluate(dataset, result)

    assert report["counts"]["P-C"] == 1


def test_duplicate_conflicting_prediction_rejected(tmp_path):
    dataset, _ = _build_case(tmp_path, ["P-C"])
    result = _write_jsonl(
        tmp_path / "conflict.jsonl",
        [{"idx": 0, "prediction": 1}, {"idx": 0, "prediction": 0}, {"idx": 1, "prediction": 0}],
    )

    with pytest.raises(DatasetError, match="重复 prediction 相互冲突"):
        evaluate(dataset, result)


def test_unknown_idx_rejected(tmp_path):
    dataset, _ = _build_case(tmp_path, ["P-C"])
    result = _write_jsonl(
        tmp_path / "unknown.jsonl",
        [{"idx": 0, "prediction": 1}, {"idx": 1, "prediction": 0}, {"idx": 999, "prediction": 1}],
    )

    with pytest.raises(DatasetError, match="未知 idx"):
        evaluate(dataset, result)


@pytest.mark.parametrize(
    ("record", "message"),
    [
        ({"prediction": 1}, "缺少字段 idx"),
        ({"idx": 0}, "缺少字段 prediction"),
        ({"idx": "0", "prediction": 1}, "idx 必须是整数"),
        ({"idx": 0, "prediction": "1"}, "prediction 必须是整数"),
        ({"idx": 0, "prediction": True}, "prediction 必须是整数"),
        ({"idx": 0, "prediction": 2}, "prediction 必须为 0 或 1"),
    ],
)
def test_result_field_validation(tmp_path, record, message):
    result = _write_jsonl(tmp_path / "bad.jsonl", [record])

    with pytest.raises(DatasetError, match=message):
        load_predictions(result)
