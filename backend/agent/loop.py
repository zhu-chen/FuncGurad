"""Agent 自主循环：模型决定工具调用与收尾，循环只负责执行、记录与门控。"""

from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass, field
from typing import Any

from backend.agent.prompts import SYSTEM_PROMPT, build_user_prompt
from backend.agent.trace import TraceWriter
from backend.config import AgentSettings
from backend.providers.base import AssistantTurn, Provider, ProviderError
from bench.dataset import Sample
from repo_tools.base import SnapshotGuard, ToolError, ToolResult
from repo_tools.registry import TOOL_SCHEMAS, dispatch, summarize


@dataclass
class AgentOutcome:
    idx: int
    target: int
    run_id: str
    provider: str
    is_stub: bool
    verdict: str
    prediction: int
    reason: str
    evidence: list[dict[str, Any]] = field(default_factory=list)
    steps: int = 0
    llm_steps: int = 0
    tool_calls: list[dict[str, Any]] = field(default_factory=list)
    timed_out: bool = False
    hit_max_steps: bool = False
    gate: dict[str, Any] = field(default_factory=dict)
    final_raw: dict[str, Any] | None = None


def parse_final_answer(content: str) -> dict[str, Any]:
    """从模型输出中提取最终 JSON；格式不合法时抛 ValueError。"""
    text = (content or "").strip()
    if text.startswith("```"):
        text = re.sub(r"^```[a-zA-Z0-9]*\s*", "", text)
        text = re.sub(r"\s*```$", "", text).strip()

    start = text.find("{")
    end = text.rfind("}")
    if start < 0 or end <= start:
        raise ValueError("未找到 JSON 对象")

    try:
        data = json.loads(text[start : end + 1])
    except json.JSONDecodeError as exc:
        raise ValueError(f"JSON 解析失败: {exc.msg}") from exc
    if not isinstance(data, dict):
        raise ValueError("最终回答必须是 JSON 对象")

    verdict = str(data.get("verdict") or "").strip().lower()
    if verdict not in ("vulnerable", "benign"):
        raise ValueError("verdict 必须为 vulnerable 或 benign")

    evidence = data.get("evidence") or []
    if not isinstance(evidence, list):
        evidence = []

    return {
        "verdict": verdict,
        "reason": str(data.get("reason") or "").strip(),
        "evidence": [item for item in evidence if isinstance(item, dict)],
    }


def apply_evidence_gate(
    guard: SnapshotGuard,
    final: dict[str, Any],
    tool_calls: list[dict[str, Any]],
) -> tuple[dict[str, Any], dict[str, Any]]:
    """没有可复核证据时，把 vulnerable 降级为 benign（对应 P-B）。"""
    if final["verdict"] != "vulnerable":
        return final, {"applied": False, "note": "模型判定 benign，无需证据门控"}

    if not any(call.get("ok") for call in tool_calls):
        gate = {"applied": True, "note": "没有任何成功的仓库工具调用"}
    elif not final["evidence"]:
        gate = {"applied": True, "note": "模型未提供 evidence"}
    else:
        valid: list[dict[str, Any]] = []
        for item in final["evidence"]:
            try:
                resolved = guard.resolve(str(item.get("path") or ""))
            except ToolError:
                continue
            if resolved.is_file():
                valid.append(item)
        if valid:
            final = dict(final)
            final["evidence"] = valid
            return final, {
                "applied": False,
                "note": f"证据可在快照中复核：{len(valid)} 条",
            }
        gate = {"applied": True, "note": "evidence 中的路径均无法在快照中验证"}

    downgraded = {
        "verdict": "benign",
        "reason": f"{final['reason']} | 证据门控：{gate['note']}，按 P-B 处理。",
        "evidence": final["evidence"],
    }
    return downgraded, gate


def _tool_message(result: ToolResult, max_chars: int) -> str:
    """把工具结果压缩成消息内容，避免单次结果撑爆上下文。"""
    payload = result.to_dict()
    data = payload.get("data")
    if isinstance(data, dict):
        compact = dict(data)
        if isinstance(compact.get("matches"), list):
            compact["matches"] = compact["matches"][:20]
        if isinstance(compact.get("lines"), list):
            compact["lines"] = compact["lines"][:300]
        compact["truncated_for_llm"] = len(
            json.dumps(data, ensure_ascii=False)
        ) > max_chars
        payload["data"] = compact

    text = json.dumps(payload, ensure_ascii=False)
    if len(text) <= max_chars:
        return text

    payload["data"] = {"truncated_for_llm": True, "summary": summarize(result)}
    return json.dumps(payload, ensure_ascii=False)[:max_chars]


def _assistant_message(turn: AssistantTurn) -> dict[str, Any]:
    message: dict[str, Any] = {
        "role": "assistant",
        "content": turn.content,
        "tool_calls": [
            {
                "id": call.id,
                "type": "function",
                "function": {
                    "name": call.name,
                    "arguments": json.dumps(call.arguments, ensure_ascii=False),
                },
            }
            for call in turn.tool_calls
        ],
    }
    # GLM 交错思考要求把上一轮的 reasoning_content 原样带回，
    # 否则工具结果之后会丢失推理上下文。
    if turn.reasoning_content:
        message["reasoning_content"] = turn.reasoning_content
    return message


def _outcome(
    sample: Sample,
    trace: TraceWriter,
    provider: Provider,
    verdict: str,
    reason: str,
    final: dict[str, Any] | None,
    steps: int,
    llm_steps: int,
    tool_calls: list[dict[str, Any]],
    timed_out: bool,
    hit_max_steps: bool,
    gate: dict[str, Any],
) -> AgentOutcome:
    prediction = 1 if verdict == "vulnerable" else 0
    return AgentOutcome(
        idx=sample.idx,
        target=sample.target,
        run_id=trace.run_id,
        provider=provider.name,
        is_stub=provider.is_stub,
        verdict=verdict,
        prediction=prediction,
        reason=reason,
        evidence=(final or {}).get("evidence") or [],
        steps=steps,
        llm_steps=llm_steps,
        tool_calls=tool_calls,
        timed_out=timed_out,
        hit_max_steps=hit_max_steps,
        gate=gate,
        final_raw=final,
    )


def run_agent(
    sample: Sample,
    guard: SnapshotGuard,
    provider: Provider,
    settings: AgentSettings,
    trace: TraceWriter,
    snapshot_display: str,
    locate_path: str | None = None,
) -> AgentOutcome:
    """运行一次自主循环；模型自己决定何时调用工具、何时收尾。"""
    deadline = time.monotonic() + settings.timeout_seconds
    messages: list[dict[str, Any]] = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {
            "role": "user",
            "content": build_user_prompt(
                sample.idx,
                sample.commit_url,
                sample.func,
                snapshot_display,
                locate_path=locate_path,
            ),
        },
    ]

    trace.emit(
        "run_start",
        payload={
            "idx": sample.idx,
            "provider": provider.name,
            "is_stub": provider.is_stub,
            "snapshot_dir": snapshot_display,
            "max_steps": settings.max_steps,
            "timeout_seconds": settings.timeout_seconds,
        },
    )

    tool_calls_log: list[dict[str, Any]] = []
    final: dict[str, Any] | None = None
    gate: dict[str, Any] = {}
    steps = 0
    llm_steps = 0
    timed_out = False
    hit_max_steps = False
    provider_error: str | None = None
    invalid_answer: str | None = None

    for step in range(1, settings.max_steps + 1):
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            timed_out = True
            trace.emit(
                "error",
                payload={"step": step, "reason": "timeout_before_llm"},
            )
            break

        steps = step
        llm_steps += 1
        final_step = step >= settings.max_steps
        if final_step:
            messages.append(
                {
                    "role": "user",
                    "content": (
                        "已达到步数上限。现在禁止调用工具，请立即只输出最终 JSON 对象："
                        '{"verdict":"vulnerable"或"benign","reason":"...",'
                        '"evidence":[{"path":"...","line":0,"note":"..."}]}。'
                        "即使证据不足也按 benign 输出，不要再展开分析。"
                    ),
                }
            )
        elif step == settings.max_steps - 1:
            messages.append(
                {
                    "role": "user",
                    "content": (
                        "这是最后一次工具调用机会；下一步必须输出最终 JSON，"
                        "不要继续展开长篇分析。"
                    ),
                }
            )
        active_tools = [] if final_step else TOOL_SCHEMAS
        started = time.perf_counter()
        try:
            turn = provider.complete(
                messages,
                active_tools,
                min(int(remaining), settings.request_timeout_seconds),
            )
        except ProviderError as exc:
            provider_error = str(exc)
            trace.emit(
                "error",
                payload={"step": step, "reason": "provider_error", "detail": provider_error},
                duration_ms=(time.perf_counter() - started) * 1000,
            )
            break

        llm_payload: dict[str, Any] = {
            "step": step,
            "finalizing": final_step,
            "content": turn.content[:2000],
            "tool_calls": [
                {"name": call.name, "arguments": call.arguments}
                for call in turn.tool_calls
            ],
        }
        if turn.reasoning_content:
            llm_payload["reasoning_content"] = turn.reasoning_content[:2000]
        usage = (turn.raw or {}).get("usage")
        if usage:
            llm_payload["usage"] = usage
        trace.emit(
            "llm_step",
            payload=llm_payload,
            duration_ms=(time.perf_counter() - started) * 1000,
        )

        if turn.tool_calls and final_step:
            trace.emit(
                "error",
                payload={
                    "step": step,
                    "reason": "tool_call_on_final_step",
                    "tools": [call.name for call in turn.tool_calls],
                },
            )

        if turn.tool_calls and not final_step:
            messages.append(_assistant_message(turn))
            for call in turn.tool_calls:
                if deadline - time.monotonic() <= 0:
                    timed_out = True
                    trace.emit(
                        "error",
                        payload={"step": step, "reason": "timeout_before_tool", "tool": call.name},
                    )
                    break

                result = dispatch(guard, call.name, call.arguments)
                summary = summarize(result)
                tool_calls_log.append(
                    {
                        "name": call.name,
                        "arguments": call.arguments,
                        "ok": result.ok,
                        "elapsed_ms": round(result.elapsed_ms, 2),
                        "summary": summary,
                    }
                )
                trace.emit(
                    "repo_tool",
                    status="ok" if result.ok else "error",
                    name=call.name,
                    payload={
                        "step": step,
                        "arguments": call.arguments,
                        "summary": summary,
                    },
                    duration_ms=result.elapsed_ms,
                )
                messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": call.id,
                        "name": call.name,
                        "content": _tool_message(
                            result, settings.max_tool_content_chars
                        ),
                    }
                )
            if timed_out:
                break
            continue

        try:
            final = parse_final_answer(turn.content)
        except ValueError as exc:
            invalid_answer = str(exc)
            trace.emit(
                "error",
                payload={"step": step, "reason": "invalid_final_answer", "detail": invalid_answer},
            )
        break

    if final is None:
        if provider_error is not None:
            reason = f"模型调用失败，已保守判定 benign：{provider_error}"
        elif timed_out:
            reason = (
                f"运行超过 {settings.timeout_seconds}s 超时保护，"
                "未得到可复核证据，按证据门控判定 benign。"
            )
        elif invalid_answer is not None:
            reason = f"模型未返回合法 JSON 结论（{invalid_answer}），保守判定 benign。"
        else:
            hit_max_steps = steps >= settings.max_steps
            reason = (
                f"达到最大步数 {settings.max_steps} 仍未收尾，"
                "按证据门控判定 benign。"
            )
        final = {"verdict": "benign", "reason": reason, "evidence": []}
        gate = {"applied": True, "note": reason}
    else:
        final, gate = apply_evidence_gate(guard, final, tool_calls_log)

    outcome = _outcome(
        sample=sample,
        trace=trace,
        provider=provider,
        verdict=final["verdict"],
        reason=final["reason"],
        final=final,
        steps=steps,
        llm_steps=llm_steps,
        tool_calls=tool_calls_log,
        timed_out=timed_out,
        hit_max_steps=hit_max_steps,
        gate=gate,
    )

    trace.emit(
        "decision",
        payload={
            "verdict": outcome.verdict,
            "prediction": outcome.prediction,
            "reason": outcome.reason,
            "evidence_count": len(outcome.evidence),
            "gate": outcome.gate,
        },
    )
    trace.emit(
        "run_end",
        status="ok" if not (timed_out or provider_error) else "error",
        payload={
            "steps": outcome.steps,
            "llm_steps": outcome.llm_steps,
            "tool_calls": len(outcome.tool_calls),
            "verdict": outcome.verdict,
            "prediction": outcome.prediction,
            "timed_out": outcome.timed_out,
            "hit_max_steps": outcome.hit_max_steps,
            "is_stub": outcome.is_stub,
        },
    )
    return outcome
