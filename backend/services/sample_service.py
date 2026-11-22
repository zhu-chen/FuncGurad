"""样本与快照元数据读取：供运行编排复用，不包含 Agent 逻辑。"""

from __future__ import annotations

import json
from pathlib import Path

from bench.dataset import Sample, iter_pairs, load_samples
from repo_service.config import REPO_ROOT
from repo_service.config import Settings as RepoServiceSettings


class SampleServiceError(RuntimeError):
    """样本、配对或快照元数据不可用。"""


def load_dataset(dataset_path: Path) -> tuple[list[Sample], dict[int, Sample]]:
    if not dataset_path.exists():
        raise SampleServiceError(f"测试集不存在: {dataset_path}")
    samples = load_samples(dataset_path)
    return samples, {sample.idx: sample for sample in samples}


def find_pair(samples: list[Sample], idx: int) -> tuple[Sample, Sample]:
    """返回 (漏洞版, 修复版)；找不到时抛错。"""
    for vulnerable, fixed in iter_pairs(samples):
        if idx in (vulnerable.idx, fixed.idx):
            return vulnerable, fixed
    raise SampleServiceError(f"idx={idx} 不在任何相邻配对中")


def load_sample_meta(settings: RepoServiceSettings, idx: int) -> dict:
    path = settings.sample_meta_dir / f"{idx}.json"
    if not path.exists():
        raise SampleServiceError(
            f"缺少样本快照元数据: {path}；请先运行 repo_service.prepare"
        )
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise SampleServiceError(f"样本元数据不是合法 JSON: {path}") from exc


def resolve_snapshot_dir(meta: dict) -> Path:
    raw = str(meta.get("snapshot_dir") or "").strip()
    if not raw:
        raise SampleServiceError("样本元数据缺少 snapshot_dir")
    path = Path(raw)
    if not path.is_absolute():
        path = REPO_ROOT / path
    return path.resolve()
