"""底线：运行产物必须同时写出 result.jsonl / report.json / trace.jsonl，且 result 记录严格只有 idx 与 prediction。"""

from __future__ import annotations

import json
from pathlib import Path

import jsonschema
import pytest

from backend.agent.loop import AgentOutcome
from backend.services import run_service
from backend.services.sample_service import SampleServiceError

REPO_ROOT = Path(__file__).resolve().parents[1]
CONTRACTS = REPO_ROOT / "shared" / "contracts"
RESULT_SCHEMA = json.loads(
    (CONTRACTS / "result_record.schema.json").read_text(encoding="utf-8")
)
TRACE_SCHEMA = json.loads(
    (CONTRACTS / "trace_event.schema.json").read_text(encoding="utf-8")
)


def _write_dataset(path: Path) -> Path:
    records = [
        {"idx": 0, "target": 1, "commit_url": "https://example.test/c/0", "func": "int f(void){}"},
        {"idx": 1, "target": 0, "commit_url": "https://example.test/c/1", "func": "int f(void){}"},
    ]
    path.write_text(
        "".join(json.dumps(item, ensure_ascii=False) + "\n" for item in records),
        encoding="utf-8",
    )
    return path


class _Provider:
    name = "scripted"
    is_stub = True
    model = "scripted-0"
    base_url = "https://example.test"


def _fake_run_agent(sample, guard, provider, settings, trace, snapshot_display, locate_path=None):
    # 用真实 TraceWriter 发两条事件，顺带覆盖 trace.jsonl 的落盘契约。
    trace.emit("run_start", payload={"idx": sample.idx})
    trace.emit(
        "decision",
        payload={"verdict": "benign", "prediction": 0, "reason": "固定测试结论"},
    )
    return AgentOutcome(
        idx=sample.idx,
        target=sample.target,
        run_id=trace.run_id,
        provider=provider.name,
        is_stub=provider.is_stub,
        verdict="benign",
        prediction=0,
        reason="固定测试结论",
    )


def _run(tmp_path: Path, monkeypatch, requested_idx: list[int] | None = None) -> dict:
    dataset = _write_dataset(tmp_path / "test.jsonl")
    snapshot = tmp_path / "snap"
    snapshot.mkdir()
    meta = {
        "method": "test",
        "locate_status": "found",
        "locate_path": "src/a.c",
        "requested_commit": "abc",
        "resolved_commit": "abc",
    }

    monkeypatch.setattr(run_service, "create_provider", lambda settings: (_Provider(), None))
    monkeypatch.setattr(run_service, "load_sample_meta", lambda settings, idx: dict(meta))
    monkeypatch.setattr(run_service, "resolve_snapshot_dir", lambda item: snapshot)
    monkeypatch.setattr(run_service, "run_agent", _fake_run_agent)
    settings = run_service.AgentSettings.from_env()
    return run_service.run(
        requested_idx if requested_idx is not None else [0],
        dataset,
        settings,
        output_dir=tmp_path / "out",
    )


def test_run_writes_all_artifacts(tmp_path, monkeypatch):
    payload = _run(tmp_path, monkeypatch)

    out_dir = Path(payload["output_dir"])
    for name in ("result.jsonl", "report.json", "trace.jsonl"):
        assert (out_dir / name).exists(), f"缺少运行产物 {name}"

    report = json.loads((out_dir / "report.json").read_text(encoding="utf-8"))
    assert report["provider"]["name"] == "scripted"
    assert report["pair"]["category"] == "P-B"
    assert [item["idx"] for item in report["runs"]] == [0, 1]


def test_result_jsonl_matches_contract(tmp_path, monkeypatch):
    payload = _run(tmp_path, monkeypatch)
    lines = [
        json.loads(line)
        for line in Path(payload["result_path"]).read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]

    assert len(lines) == 2
    for record in lines:
        assert set(record) == {"idx", "prediction"}
        jsonschema.validate(instance=record, schema=RESULT_SCHEMA)


@pytest.mark.parametrize(
    "record",
    [
        {"idx": 0, "prediction": 0, "reason": "多余字段"},
        {"prediction": 0},
        {"idx": 0},
        {"idx": "0", "prediction": 0},
        {"idx": 0, "prediction": 2},
        {"idx": -1, "prediction": 0},
    ],
)
def test_result_record_contract_rejects_invalid(record):
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(instance=record, schema=RESULT_SCHEMA)


def test_trace_jsonl_matches_contract(tmp_path, monkeypatch):
    payload = _run(tmp_path, monkeypatch)
    events = [
        json.loads(line)
        for line in Path(payload["trace_path"]).read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]

    assert events, "trace.jsonl 不应为空"
    for event in events:
        jsonschema.validate(instance=event, schema=TRACE_SCHEMA)

    # seq 只需在同一个 run_id 内从 0 单调递增；不同样本的 run 会各自从 0 开始。
    by_run: dict[str, list[int]] = {}
    for event in events:
        by_run.setdefault(event["run_id"], []).append(event["seq"])
    assert len(by_run) == 2
    for seqs in by_run.values():
        assert seqs == list(range(len(seqs)))


def test_unknown_idx_rejected(tmp_path, monkeypatch):
    with pytest.raises(SampleServiceError, match="不在测试集中"):
        _run(tmp_path, monkeypatch, requested_idx=[99])
