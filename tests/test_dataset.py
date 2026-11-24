"""底线：测试集读取必须成对、字段严格，缺文件时报可读错误而不是静默通过。"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from bench.dataset import DatasetError, iter_pairs, load_samples

REPO_ROOT = Path(__file__).resolve().parents[1]
REAL_DATASET = REPO_ROOT / "cache" / "dataset" / "test.jsonl"


def _write(tmp_path: Path, records: list[dict], name: str = "test.jsonl") -> Path:
    path = tmp_path / name
    path.write_text(
        "".join(json.dumps(record, ensure_ascii=False) + "\n" for record in records),
        encoding="utf-8",
    )
    return path


def _sample(idx: int, target: int, overrides: dict | None = None) -> dict:
    record = {
        "idx": idx,
        "commit_url": f"https://github.com/example/repo/commit/{idx:040x}",
        "target": target,
        "func": f"int func_{idx}(void) {{ return {target}; }}",
    }
    if overrides:
        record.update(overrides)
    return record


def test_loads_pairs_and_identity_follows_target(tmp_path):
    # 故意把修复版放在前面，确认按 target 而不是顺序识别身份。
    path = _write(tmp_path, [_sample(0, 0), _sample(1, 1)])

    samples = load_samples(path)
    pairs = list(iter_pairs(samples))

    assert len(samples) == 2
    assert pairs[0][0].idx == 1 and pairs[0][0].target == 1
    assert pairs[0][1].idx == 0 and pairs[0][1].target == 0


def test_missing_file_raises_filenotfound(tmp_path):
    missing = tmp_path / "nope.jsonl"

    with pytest.raises(FileNotFoundError) as excinfo:
        load_samples(missing)

    assert "nope.jsonl" in str(excinfo.value)


def test_empty_file_rejected(tmp_path):
    path = tmp_path / "empty.jsonl"
    path.write_text("", encoding="utf-8")

    with pytest.raises(DatasetError, match="测试集为空"):
        load_samples(path)


def test_odd_record_count_rejected(tmp_path):
    path = _write(tmp_path, [_sample(0, 1), _sample(1, 0), _sample(2, 1)])

    with pytest.raises(DatasetError, match="必须为偶数"):
        load_samples(path)


def test_pair_without_both_targets_rejected(tmp_path):
    path = _write(tmp_path, [_sample(0, 1), _sample(1, 1)])

    with pytest.raises(DatasetError, match="必须包含一个 target=1 和一个 target=0"):
        load_samples(path)


def test_duplicate_conflicting_idx_rejected(tmp_path):
    path = _write(tmp_path, [_sample(0, 1), _sample(1, 0), _sample(0, 0), _sample(2, 1)])

    with pytest.raises(DatasetError, match="对应不同样本"):
        load_samples(path)


def test_duplicate_identical_idx_allowed(tmp_path):
    path = _write(tmp_path, [_sample(0, 1), _sample(1, 0), _sample(0, 1), _sample(1, 0)])

    samples = load_samples(path)

    assert len(samples) == 4


@pytest.mark.parametrize(
    ("overrides", "message"),
    [
        ({"target": 2}, "target 必须为 0 或 1"),
        ({"target": True}, "target 必须是整数"),
        ({"idx": "1"}, "idx 必须是整数"),
        ({"commit_url": ""}, "commit_url 必须是非空字符串"),
        ({"func": None}, "func 必须是非空字符串"),
    ],
)
def test_field_validation(tmp_path, overrides, message):
    path = _write(tmp_path, [_sample(0, 1, overrides), _sample(1, 0)])

    with pytest.raises(DatasetError, match=message):
        load_samples(path)


def test_malformed_json_reports_line_number(tmp_path):
    path = tmp_path / "bad.jsonl"
    valid = json.dumps(_sample(0, 1, {"target": 1}), ensure_ascii=False)
    path.write_text(valid + "\nnot-json\n", encoding="utf-8")

    with pytest.raises(DatasetError, match="bad.jsonl:2"):
        load_samples(path)


@pytest.mark.skipif(
    not REAL_DATASET.exists(),
    reason="需要课程测试集 cache/dataset/test.jsonl；干净 clone 上默认跳过",
)
def test_real_dataset_shape():
    samples = load_samples(REAL_DATASET)

    assert len(samples) == 300
    assert len({sample.idx for sample in samples}) == 300
    pairs = list(iter_pairs(samples))
    assert len(pairs) == 150
    assert all(vulnerable.target == 1 and fixed.target == 0 for vulnerable, fixed in pairs)
