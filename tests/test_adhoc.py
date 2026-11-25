"""临时样本通路的底线：输入校验、逐行解析、离线跑通产物契约。

全部用例使用 tmp_path 与 stub provider，不需要课程数据集、快照、索引、网络或 key。
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from backend.services import adhoc_service
from backend.services.adhoc_service import (
    AdhocError,
    AdhocSpec,
    parse_upload,
    run_adhoc,
    spec_from_manual,
)
from repo_service.snapshot import SnapshotOutcome

FUNC = "int demo_read(int idx) { char buf[4]; return buf[idx]; }\n"


# ---------- 输入校验 ----------


def test_spec_from_manual_maps_version_and_defaults():
    vulnerable = spec_from_manual(FUNC, "https://github.com/a/b/commit/" + "a" * 40, "vulnerable")
    assert vulnerable.target == 1
    assert vulnerable.version == "vulnerable"
    assert vulnerable.idx == 0
    assert vulnerable.provided_idx is False

    fixed = spec_from_manual(
        FUNC, "https://github.com/a/b/commit/" + "a" * 40, "fixed", idx=7
    )
    assert fixed.target == 0
    assert fixed.version == "fixed"
    assert fixed.idx == 7
    assert fixed.provided_idx is True


@pytest.mark.parametrize(
    "func,url,version",
    [
        ("", "https://github.com/a/b/commit/x", "vulnerable"),
        ("   ", "https://github.com/a/b/commit/x", "vulnerable"),
        (FUNC, "", "vulnerable"),
        (FUNC, "ftp://example.test/x", "vulnerable"),
        (FUNC, "https://github.com/a/b/commit/x", "both"),
    ],
)
def test_spec_from_manual_rejects_bad_input(func, url, version):
    with pytest.raises(AdhocError):
        spec_from_manual(func, url, version)


def test_spec_from_manual_rejects_negative_idx():
    with pytest.raises(AdhocError):
        spec_from_manual(FUNC, "https://github.com/a/b/commit/x", "vulnerable", idx=-1)


# ---------- 上传 jsonl 解析 ----------


def _row(idx: int, target: int) -> dict:
    return {
        "idx": idx,
        "target": target,
        "commit_url": f"https://github.com/a/b/commit/{idx:040d}",
        "func": FUNC,
    }


def test_parse_upload_accepts_rows_without_requiring_pairs():
    content = "\n".join(
        [
            json.dumps(_row(3, 1)),
            "",
            json.dumps(_row(4, 0)),
            json.dumps(_row(9, 1)),
        ]
    )
    specs = parse_upload(content)
    assert [spec.idx for spec in specs] == [3, 4, 9]
    assert [spec.target for spec in specs] == [1, 0, 1]
    assert all(spec.provided_idx for spec in specs)


@pytest.mark.parametrize(
    "content,message",
    [
        ("", "上传内容为空"),
        ("not json\n", "第 1 行 JSON 格式错误"),
        (json.dumps({"target": 1, "commit_url": "https://x.test/c", "func": FUNC}), "第 1 行 idx"),
        (json.dumps({"idx": 1, "target": 2, "commit_url": "https://x.test/c", "func": FUNC}), "第 1 行 target"),
        (json.dumps({"idx": 1, "target": 1, "commit_url": "https://x.test/c", "func": ""}), "函数代码不能为空"),
        (json.dumps({"idx": True, "target": 1, "commit_url": "https://x.test/c", "func": FUNC}), "第 1 行 idx"),
    ],
)
def test_parse_upload_reports_readable_errors(content, message):
    with pytest.raises(AdhocError) as excinfo:
        parse_upload(content)
    assert message in str(excinfo.value)


def test_parse_upload_rejects_duplicate_idx():
    line = json.dumps(_row(5, 1))
    with pytest.raises(AdhocError) as excinfo:
        parse_upload(line + "\n" + line + "\n")
    assert "重复" in str(excinfo.value)


def test_parse_upload_rejects_too_many_samples():
    content = "\n".join(json.dumps(_row(idx, 1)) for idx in range(201))
    with pytest.raises(AdhocError) as excinfo:
        parse_upload(content)
    assert "最多 200 条" in str(excinfo.value)


# ---------- 离线跑通 ----------


def _settings(tmp_path: Path, monkeypatch):
    from backend.config import AgentSettings

    monkeypatch.setenv("FUNCGUARD_CACHE_DIR", str(tmp_path / "cache"))
    monkeypatch.setenv("FUNCGUARD_AGENT_RUNS_DIR", str(tmp_path / "cache" / "runs"))
    monkeypatch.setenv("FUNCGUARD_LLM_PROVIDER", "stub")
    return AgentSettings.from_env()


def _fake_manager(snapshot_dir: Path | None, status: str = "snapshot_ok"):
    class FakeManager:
        def __init__(self, settings):
            self.settings = settings

        def ensure_snapshot(self, sample):
            if status != "snapshot_ok" or snapshot_dir is None:
                return SnapshotOutcome(
                    status="failed",
                    ref=None,
                    error="模拟的网络失败：GitHub 429",
                )
            return SnapshotOutcome(
                status="snapshot_ok",
                ref=None,
                requested_commit="a" * 40,
                resolved_commit="b" * 40,
                version_role="vulnerable" if sample.target == 1 else "fixed",
                snapshot_dir=snapshot_dir,
                method="fake",
            )

    return FakeManager


def _snapshot_with_func(tmp_path: Path) -> Path:
    snapshot = tmp_path / "snapshot"
    snapshot.mkdir()
    (snapshot / "demo.c").write_text(FUNC, encoding="utf-8")
    return snapshot


def test_run_adhoc_writes_contract_artifacts(tmp_path, monkeypatch):
    snapshot = _snapshot_with_func(tmp_path)
    monkeypatch.setattr(adhoc_service, "SnapshotManager", _fake_manager(snapshot))
    settings = _settings(tmp_path, monkeypatch)
    spec = AdhocSpec(
        idx=42,
        commit_url="https://github.com/a/b/commit/" + "a" * 40,
        target=1,
        func=FUNC,
    )
    stages: list[str] = []

    payload = run_adhoc(
        [spec],
        settings,
        output_dir=tmp_path / "run",
        mode="manual",
        on_progress=stages.append,
    )

    assert stages == ["preparing_snapshot", "running"]
    assert payload["failures"] == []
    assert len(payload["runs"]) == 1
    run = payload["runs"][0]
    assert run["idx"] == 42
    assert run["version"] == "vulnerable"
    assert run["locate_path"] == "demo.c"

    result_lines = (tmp_path / "run" / "result.jsonl").read_text(encoding="utf-8").splitlines()
    assert len(result_lines) == 1
    record = json.loads(result_lines[0])
    assert set(record) == {"idx", "prediction"}
    assert record["idx"] == 42
    assert record["prediction"] in (0, 1)

    report = json.loads((tmp_path / "run" / "report.json").read_text(encoding="utf-8"))
    assert report["mode"] == "manual"
    assert report["provider"]["is_stub"] is True
    assert report["input"]["samples"] == 1
    assert report["runs"][0]["verdict"] in ("vulnerable", "benign")

    events = [
        json.loads(line)
        for line in (tmp_path / "run" / "trace.jsonl").read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    assert events
    assert {event["type"] for event in events} >= {"run_start", "run_end"}


def test_run_adhoc_records_failure_and_keeps_going(tmp_path, monkeypatch):
    monkeypatch.setattr(
        adhoc_service, "SnapshotManager", _fake_manager(None, status="failed")
    )
    settings = _settings(tmp_path, monkeypatch)
    spec = AdhocSpec(
        idx=0,
        commit_url="https://github.com/a/b/commit/" + "a" * 40,
        target=1,
        func=FUNC,
        provided_idx=False,
    )

    payload = run_adhoc([spec], settings, output_dir=tmp_path / "run", mode="manual")

    assert payload["runs"] == []
    assert len(payload["failures"]) == 1
    assert "429" in payload["failures"][0]["error"]
    report = json.loads((tmp_path / "run" / "report.json").read_text(encoding="utf-8"))
    assert report["failures"][0]["idx"] == 0
    assert (tmp_path / "run" / "result.jsonl").read_text(encoding="utf-8") == ""
