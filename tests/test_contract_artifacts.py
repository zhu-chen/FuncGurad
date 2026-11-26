"""底线：运行产物必须同时写出 result.jsonl / report.json / trace.jsonl，且 result 记录严格只有 idx 与 prediction。"""

from __future__ import annotations

import json
from pathlib import Path

import jsonschema
import pytest

from backend.providers.base import AssistantTurn, ToolCall
from backend.providers.stub import StubProvider
from backend.services import run_service
from backend.services.sample_service import SampleServiceError
from rag.bm25 import BM25Index

REPO_ROOT = Path(__file__).resolve().parents[1]
CONTRACTS = REPO_ROOT / "shared" / "contracts"
RESULT_SCHEMA = json.loads(
    (CONTRACTS / "result_record.schema.json").read_text(encoding="utf-8")
)
TRACE_SCHEMA = json.loads(
    (CONTRACTS / "trace_event.schema.json").read_text(encoding="utf-8")
)
REPORT_SCHEMA = json.loads(
    (CONTRACTS / "run_report.schema.json").read_text(encoding="utf-8")
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

    def __init__(self, tool_order: tuple[str, ...] = ()) -> None:
        self.tool_order = tool_order
        self.first_turns = []

    def complete(self, messages, tools, timeout_seconds):
        completed = sum(message["role"] == "tool" for message in messages)
        offered = {tool["function"]["name"] for tool in tools}
        if completed == 0:
            self.first_turns.append((messages[0]["content"], messages[1]["content"], offered))
        if completed == len(self.tool_order):
            return AssistantTurn(content='{"verdict":"benign","reason":"固定测试结论","evidence":[]}')
        name = self.tool_order[completed]
        assert name in offered
        arguments = {"query": "函数"} if name == "search_knowledge" else {"path": "src/a.c"}
        return AssistantTurn(tool_calls=[ToolCall(id=str(completed), name=name, arguments=arguments)])


def _run(
    tmp_path: Path, monkeypatch, requested_idx: list[int] | None = None, *,
    provider=None, knowledge_available: bool = False, profile: str = "retrieval_first",
) -> dict:
    dataset = _write_dataset(tmp_path / "test.jsonl")
    snapshot = tmp_path / "snap"
    (snapshot / "src").mkdir(parents=True)
    (snapshot / "src" / "a.c").write_text("int example(void) { return 0; }\n", encoding="utf-8")
    knowledge_dir = tmp_path / "knowledge"
    if knowledge_available:
        knowledge_dir.mkdir()
        (knowledge_dir / "cards.jsonl").write_text("", encoding="utf-8")
        BM25Index.build([]).save(knowledge_dir / "bm25.json")
    meta = {
        "method": "test",
        "locate_status": "found",
        "locate_path": "src/a.c",
        "requested_commit": "abc",
        "resolved_commit": "abc",
    }

    provider = provider or _Provider()
    monkeypatch.setattr(run_service, "create_provider", lambda settings: (provider, None))
    monkeypatch.setattr(run_service, "load_sample_meta", lambda settings, idx: dict(meta))
    monkeypatch.setattr(run_service, "resolve_snapshot_dir", lambda item: snapshot)
    monkeypatch.setenv("FUNCGUARD_KNOWLEDGE_DIR", str(knowledge_dir))
    monkeypatch.setenv("FUNCGUARD_AGENT_PROMPT_PROFILE", profile)
    monkeypatch.setenv("FUNCGUARD_AGENT_MAX_STEPS", "6")
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
    jsonschema.validate(instance=report, schema=REPORT_SCHEMA)

    for item in report["runs"]:
        del item["flow"]
    jsonschema.validate(instance=report, schema=REPORT_SCHEMA)


@pytest.mark.parametrize(
    ("order", "available", "knowledge_before_repo"),
    [
        (("search_knowledge", "read_file"), True, True),
        (("read_file", "search_knowledge"), True, False),
        (("read_file",), True, False),
        (("read_file",), False, False),
        (("search_knowledge",), True, None),
        ((), True, None),
    ],
)
def test_report_and_trace_record_actual_tool_order(
    tmp_path, monkeypatch, order, available, knowledge_before_repo
):
    provider = _Provider(order)
    payload = _run(tmp_path, monkeypatch, provider=provider, knowledge_available=available)
    report = json.loads(Path(payload["report_path"]).read_text(encoding="utf-8"))
    events = [
        json.loads(line)
        for line in Path(payload["trace_path"]).read_text(encoding="utf-8").splitlines()
    ]
    expected = {
        "prompt_profile": "retrieval_first",
        "knowledge_available": available,
        "knowledge_calls": order.count("search_knowledge"),
        "repo_tool_calls": order.count("read_file"),
        "first_tool": order[0] if order else None,
        "knowledge_before_repo": knowledge_before_repo,
    }
    jsonschema.validate(instance=report, schema=REPORT_SCHEMA)
    for run in report["runs"]:
        assert run["flow"] == expected
        assert tuple(call["name"] for call in run["tool_calls"]) == order
        trace = [event for event in events if event["run_id"] == run["run_id"]]
        assert tuple(event["name"] for event in trace if event["type"] == "repo_tool") == order
        assert trace[-1]["payload"]["flow"] == expected
    for system_prompt, user_prompt, offered in provider.first_turns:
        assert ("search_knowledge" in offered) == available
        assert ("search_knowledge" in (system_prompt + user_prompt)) == available
        assert system_prompt.index("初步判断") < system_prompt.index("find_files")
        if available:
            assert system_prompt.index("search_knowledge") < system_prompt.index("初步判断")


@pytest.mark.parametrize("available", [True, False])
def test_stub_searches_first_with_a_local_index(tmp_path, monkeypatch, available):
    payload = _run(
        tmp_path, monkeypatch, provider=StubProvider(), knowledge_available=available
    )
    expected = ["find_files", "grep_content", "read_file"]
    if available:
        expected.insert(0, "search_knowledge")
    for run in payload["runs"]:
        assert [call["name"] for call in run["tool_calls"]] == expected
        assert all(call["ok"] for call in run["tool_calls"])


def test_legacy_profile_is_used_at_runtime(tmp_path, monkeypatch):
    provider = _Provider()
    payload = _run(tmp_path, monkeypatch, provider=provider, profile="legacy")
    for system_prompt, user_prompt, _ in provider.first_turns:
        assert "先根据待分析函数定位" in system_prompt
        assert "请使用工具在快照中定位" in user_prompt
    assert all(run["flow"]["prompt_profile"] == "legacy" for run in payload["runs"])


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
