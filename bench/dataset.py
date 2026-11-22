"""测试集读取、校验与配对。

字段与类型以 shared/contracts/sample.schema.json 为准；
统计口径与官方 evaluation.py 保持一致：
- 按测试集相邻两条记录配对；
- 每对中 target=1 为漏洞版，target=0 为修复版；
- 身份由 target 决定，不由前后顺序决定。
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator


class DatasetError(ValueError):
    """测试集或结果文件不符合契约。"""


@dataclass(frozen=True)
class Sample:
    """测试集单条样本。"""

    idx: int
    commit_url: str
    target: int
    func: str


def read_jsonl(path: Path) -> Iterator[tuple[str, dict]]:
    """逐条读取 JSON 对象，保留行号以便定位格式错误。"""
    with path.open("r", encoding="utf-8-sig") as stream:
        for line_number, line in enumerate(stream, 1):
            if not line.strip():
                continue
            location = f"{path}:{line_number}"
            try:
                record = json.loads(line)
            except json.JSONDecodeError as exc:
                raise DatasetError(f"{location}: JSON 格式错误：{exc.msg}") from exc
            if not isinstance(record, dict):
                raise DatasetError(f"{location}: 每条记录必须是 JSON 对象")
            yield location, record


def require_int(record: dict, field: str, location: str) -> int:
    """严格校验整数，避免把布尔值、浮点数或字符串当作标签。"""
    if field not in record:
        raise DatasetError(f"{location}: 缺少字段 {field}")
    value = record[field]
    if type(value) is not int:
        raise DatasetError(f"{location}: {field} 必须是整数")
    return value


def load_samples(path: Path) -> list[Sample]:
    """加载并校验测试集；校验不通过时抛出 DatasetError。"""
    samples: list[Sample] = []
    seen: dict[int, Sample] = {}

    for location, record in read_jsonl(path):
        idx = require_int(record, "idx", location)
        target = require_int(record, "target", location)
        if target not in (0, 1):
            raise DatasetError(f"{location}: target 必须为 0 或 1")

        for field in ("commit_url", "func"):
            value = record.get(field)
            if not isinstance(value, str) or not value:
                raise DatasetError(f"{location}: {field} 必须是非空字符串")

        sample = Sample(
            idx=idx,
            commit_url=record["commit_url"],
            target=target,
            func=record["func"],
        )
        if idx in seen and seen[idx] != sample:
            raise DatasetError(f"{location}: idx={idx} 对应不同样本，无法唯一匹配")
        seen[idx] = sample
        samples.append(sample)

    if not samples:
        raise DatasetError(f"{path}: 测试集为空")
    if len(samples) % 2:
        raise DatasetError(f"{path}: 测试集有 {len(samples)} 条记录，必须为偶数才能配对")

    for offset in range(0, len(samples), 2):
        first, second = samples[offset], samples[offset + 1]
        if {first.target, second.target} != {0, 1}:
            raise DatasetError(
                f"{path}: 第 {offset // 2 + 1} 对（idx={first.idx}, {second.idx}）"
                "必须包含一个 target=1 和一个 target=0"
            )
    return samples


def iter_pairs(samples: list[Sample]) -> Iterator[tuple[Sample, Sample]]:
    """按相邻顺序配对，返回 (漏洞版, 修复版)。"""
    for offset in range(0, len(samples), 2):
        first, second = samples[offset], samples[offset + 1]
        vulnerable, fixed = (first, second) if first.target == 1 else (second, first)
        yield vulnerable, fixed
