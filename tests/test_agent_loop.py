"""底线：Agent 循环的收尾必须保守——没有可复核证据时不许给出 vulnerable，且必须有步数/超时上限。"""

from __future__ import annotations

import dataclasses
import json
import time
from pathlib import Path

import pytest

from backend.agent.loop import parse_final_answer, run_agent
from backend.agent.trace import TraceWriter
from backend.config import AgentSettings
from backend.providers.base import AssistantTurn, Provider, ProviderError, ToolCall
from bench.dataset import Sample
from repo_tools.base import SnapshotGuard, ToolLimits


def _settings(**overrides) -> AgentSettings:
    base = AgentSettings.from_env()
    values = {
        "max_steps": 3,
        "timeout_seconds": 30,
        "request_timeout_seconds": 5,
        "api_key": "",
        "provider": "scripted",
    }
    values.update(overrides)
    return dataclasses.replace(base, **values)


def _sample(idx: int = 0, target: int = 1) -> Sample:
    return Sample(
        idx=idx,
        commit_url=f"https://github.com/example/repo/commit/{idx:040x}",
        target=target,
        func="int copy(char *dst, const char *src) { return 0; }",
    )


def _snapshot(tmp_path: Path) -> SnapshotGuard:
    root = tmp_path / "snap"
    (root / "src").mkdir(parents=True)
    (root / "src" / "a.c").write_text("int main(void) {\n  return 0;\n}\n", encoding="utf-8")
    return SnapshotGuard(root, ToolLimits())


def _final(verdict: str, evidence: list[dict] | None = None) -> AssistantTurn:
    payload = {
        "verdict": verdict,
        "reason": "测试用例固定结论",
        "evidence": evidence or [],
    }
    return AssistantTurn(content=json.dumps(payload, ensure_ascii=False))


class ScriptedProvider(Provider):
    """按脚本逐次返回助手消息的假 provider；不联网、不读 key。"""

    name = "scripted"
    is_stub = True

    def __init__(self, turns: list[AssistantTurn]) -> None:
        self.turns = list(turns)
        self.calls = 0

    def complete(self, messages, tools, timeout_seconds) -> AssistantTurn:
        turn = self.turns[min(self.calls, len(self.turns) - 1)]
        self.calls += 1
        return turn


class AlwaysToolProvider(ScriptedProvider):
    """每一步都返回工具调用，用来验证最大步数保护。"""

    def __init__(self, delay: float = 0.0) -> None:
        super().__init__([AssistantTurn()])
        self.delay = delay

    def complete(self, messages, tools, timeout_seconds) -> AssistantTurn:
        if self.delay:
            time.sleep(self.delay)
        self.calls += 1
        return AssistantTurn(
            tool_calls=[ToolCall(id=f"call-{self.calls}", name="find_files", arguments={"pattern": "*.c"})]
        )


class ExplodingProvider(Provider):
    name = "exploding"
    is_stub = True

    def __init__(self) -> None:
        self.calls = 0

    def complete(self, messages, tools, timeout_seconds) -> AssistantTurn:
        self.calls += 1
        raise ProviderError("模拟模型服务不可用")


def test_vulnerable_without_tool_calls_is_downgraded(tmp_path):
    # 无任何成功工具调用却报 vulnerable：必须按 P-B 降级为 benign。
    trace = TraceWriter("run-no-tool")
    outcome = run_agent(
        sample=_sample(),
        guard=_snapshot(tmp_path),
        provider=ScriptedProvider(
            [_final("vulnerable", [{"path": "src/a.c", "line": 1, "note": "存在越界写"}] )]
        ),
        settings=_settings(),
        trace=trace,
        snapshot_display="snap",
    )

    assert outcome.prediction == 0
    assert outcome.verdict == "benign"
    assert outcome.gate["applied"] is True
    assert "没有任何成功的仓库工具调用" in outcome.gate["note"]


def test_vulnerable_with_verified_evidence_is_kept(tmp_path):
    trace = TraceWriter("run-with-evidence")
    provider = ScriptedProvider(
        [
            AssistantTurn(
                tool_calls=[ToolCall(id="c1", name="read_file", arguments={"path": "src/a.c"})]
            ),
            _final("vulnerable", [{"path": "src/a.c", "line": 1, "note": "存在越界写"}]),
        ]
    )

    outcome = run_agent(
        sample=_sample(),
        guard=_snapshot(tmp_path),
        provider=provider,
        settings=_settings(max_steps=2),
        trace=trace,
        snapshot_display="snap",
    )

    assert outcome.prediction == 1
    assert outcome.verdict == "vulnerable"
    assert outcome.gate["applied"] is False
    assert outcome.hit_max_steps is True
    assert [item["path"] for item in outcome.evidence] == ["src/a.c"]
    assert any(call["ok"] for call in outcome.tool_calls)


def test_vulnerable_with_unverifiable_evidence_is_downgraded(tmp_path):
    trace = TraceWriter("run-bad-evidence")
    provider = ScriptedProvider(
        [
            AssistantTurn(
                tool_calls=[ToolCall(id="c1", name="read_file", arguments={"path": "src/a.c"})]
            ),
            _final("vulnerable", [{"path": "../secret.txt", "line": 1, "note": "越界证据"}]),
        ]
    )

    outcome = run_agent(
        sample=_sample(),
        guard=_snapshot(tmp_path),
        provider=provider,
        settings=_settings(),
        trace=trace,
        snapshot_display="snap",
    )

    assert outcome.prediction == 0
    assert outcome.gate["applied"] is True
    assert "无法在快照中验证" in outcome.gate["note"]


def test_vulnerable_with_tool_but_empty_evidence_is_downgraded(tmp_path):
    trace = TraceWriter("run-empty-evidence")
    provider = ScriptedProvider(
        [
            AssistantTurn(
                tool_calls=[ToolCall(id="c1", name="read_file", arguments={"path": "src/a.c"})]
            ),
            _final("vulnerable"),
        ]
    )

    outcome = run_agent(
        sample=_sample(),
        guard=_snapshot(tmp_path),
        provider=provider,
        settings=_settings(),
        trace=trace,
        snapshot_display="snap",
    )

    assert outcome.prediction == 0
    assert outcome.gate["applied"] is True
    assert "未提供 evidence" in outcome.gate["note"]


def test_benign_is_not_affected_by_gate(tmp_path):
    trace = TraceWriter("run-benign")
    outcome = run_agent(
        sample=_sample(),
        guard=_snapshot(tmp_path),
        provider=ScriptedProvider([_final("benign")]),
        settings=_settings(),
        trace=trace,
        snapshot_display="snap",
    )

    assert outcome.prediction == 0
    assert outcome.gate["applied"] is False
    assert outcome.hit_max_steps is False


def test_max_steps_terminates_without_looping(tmp_path):
    provider = AlwaysToolProvider()
    trace = TraceWriter("run-max-steps")

    outcome = run_agent(
        sample=_sample(),
        guard=_snapshot(tmp_path),
        provider=provider,
        settings=_settings(max_steps=2),
        trace=trace,
        snapshot_display="snap",
    )

    # 关键底线：步数用尽后必须收敛为 benign，而不是继续调用模型。
    assert provider.calls == 2
    assert outcome.steps == 2
    assert outcome.timed_out is False
    assert outcome.prediction == 0
    assert outcome.gate["applied"] is True


def test_hit_max_steps_flag_reports_true_on_exhaustion(tmp_path):
    provider = AlwaysToolProvider()

    outcome = run_agent(
        sample=_sample(),
        guard=_snapshot(tmp_path),
        provider=provider,
        settings=_settings(max_steps=2),
        trace=TraceWriter("run-hit-max-steps"),
        snapshot_display="snap",
    )

    assert outcome.hit_max_steps is True


def test_total_timeout_terminates_without_looping(tmp_path):
    provider = AlwaysToolProvider(delay=1.05)
    trace = TraceWriter("run-timeout")

    started = time.monotonic()
    outcome = run_agent(
        sample=_sample(),
        guard=_snapshot(tmp_path),
        provider=provider,
        settings=_settings(max_steps=5, timeout_seconds=1),
        trace=trace,
        snapshot_display="snap",
    )
    elapsed = time.monotonic() - started

    assert elapsed < 5
    assert outcome.timed_out is True
    assert outcome.hit_max_steps is False
    assert outcome.prediction == 0
    assert "超时" in outcome.reason


def test_provider_error_falls_back_to_benign(tmp_path):
    provider = ExplodingProvider()
    outcome = run_agent(
        sample=_sample(),
        guard=_snapshot(tmp_path),
        provider=provider,
        settings=_settings(),
        trace=TraceWriter("run-provider-error"),
        snapshot_display="snap",
    )

    assert provider.calls == 1
    assert outcome.hit_max_steps is False
    assert outcome.verdict == "benign"
    assert outcome.prediction == 0
    assert "模型调用失败" in outcome.reason
    assert outcome.gate["applied"] is True


def test_invalid_final_json_falls_back_to_benign(tmp_path):
    outcome = run_agent(
        sample=_sample(),
        guard=_snapshot(tmp_path),
        provider=ScriptedProvider([AssistantTurn(content="我觉得这里可能有漏洞")]),
        settings=_settings(),
        trace=TraceWriter("run-invalid"),
        snapshot_display="snap",
    )

    assert outcome.verdict == "benign"
    assert outcome.prediction == 0
    assert "未返回合法 JSON 结论" in outcome.reason


def test_parse_final_answer_accepts_code_fence():
    parsed = parse_final_answer(
        '```json\n{"verdict":"vulnerable","reason":"越界写","evidence":[]}\n```'
    )

    assert parsed["verdict"] == "vulnerable"
    assert parsed["reason"] == "越界写"


@pytest.mark.parametrize(
    "content",
    [
        "",
        "no json here",
        '{"reason":"缺少 verdict"}',
        '{"verdict":"unknown"}',
    ],
)
def test_parse_final_answer_rejects_invalid(content):
    with pytest.raises(ValueError):
        parse_final_answer(content)
