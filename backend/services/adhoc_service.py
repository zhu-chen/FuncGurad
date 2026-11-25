"""手工单样本与上传 .jsonl 的临时样本通路。

与按数据集评分的 `run_service` 分开：

- 这里不读 `dataset/test.jsonl`，只把请求转成 `Sample` 后复用现有快照准备、
  函数定位、Agent 循环、证据门控与轨迹；
- 产物只写本次 run 目录（result.jsonl / report.json / trace.jsonl），
  不参与全量评测，也不改 `run_service` 的评分路径。

课程约束仍然生效：每次推理只给一个版本的函数与该版本快照。手工单样本由用户
显式选择版本（修复前 / 修复后），上传样本按每条记录的 `target` 决定。
"""

from __future__ import annotations

import json
import time
import uuid
from dataclasses import dataclass, replace
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

from backend.agent.loop import run_agent
from backend.agent.trace import TraceWriter
from backend.config import AgentSettings
from backend.providers import create_provider
from bench.dataset import Sample
from repo_service.config import Settings as RepoServiceSettings
from repo_service.config import display_path
from repo_service.locator import locate_function
from repo_service.snapshot import SnapshotManager
from repo_tools.base import SnapshotGuard, ToolLimits
from repo_tools.registry import tool_names

VERSION_TO_TARGET = {"vulnerable": 1, "fixed": 0}
TARGET_TO_VERSION = {1: "vulnerable", 0: "fixed"}

# 手工样本没有 idx 时用于报告展示；result.jsonl 契约要求整数且 >= 0。
MANUAL_DEFAULT_IDX = 0

MAX_FUNC_CHARS = 200_000
MAX_COMMIT_URL_CHARS = 2048
MAX_UPLOAD_CHARS = 8_000_000
MAX_UPLOAD_SAMPLES = 200

ProgressHook = Callable[[str], None]

# 同一进程内复用快照管理器：上传批量逐条执行时，仓库克隆与
# 「git 链路不可用」的判定不必每条重来。
_MANAGERS: dict[str, SnapshotManager] = {}


def _manager_for(settings: RepoServiceSettings) -> SnapshotManager:
    key = str(settings.cache_dir)
    manager = _MANAGERS.get(key)
    if manager is None:
        manager = SnapshotManager(settings)
        _MANAGERS[key] = manager
    return manager


class AdhocError(RuntimeError):
    """临时样本请求不合法或无法准备快照。"""

    def __init__(self, message: str, status_code: int = 400) -> None:
        super().__init__(message)
        self.status_code = status_code


@dataclass(frozen=True)
class AdhocSpec:
    """一次临时推理的输入；字段与 Sample 对齐，另记版本与 idx 是否来自用户。"""

    idx: int
    commit_url: str
    target: int
    func: str
    provided_idx: bool = True

    @property
    def version(self) -> str:
        return TARGET_TO_VERSION[self.target]


def _validate_common(func: str, commit_url: str) -> tuple[str, str]:
    func = (func or "").strip()
    commit_url = (commit_url or "").strip()
    if not func:
        raise AdhocError("函数代码不能为空")
    if len(func) > MAX_FUNC_CHARS:
        raise AdhocError(
            f"函数代码过长（{len(func)} 字符），上限 {MAX_FUNC_CHARS} 字符"
        )
    if not commit_url:
        raise AdhocError("修复提交链接不能为空")
    if len(commit_url) > MAX_COMMIT_URL_CHARS:
        raise AdhocError(
            f"修复提交链接过长（{len(commit_url)} 字符），上限 {MAX_COMMIT_URL_CHARS} 字符"
        )
    if not commit_url.lower().startswith(("http://", "https://")):
        raise AdhocError("修复提交链接必须是 http(s) 地址")
    return func, commit_url


def spec_from_manual(
    func: str,
    commit_url: str,
    version: str,
    idx: int | None = None,
) -> AdhocSpec:
    """把界面上的手工输入转成 AdhocSpec；版本必须显式选择。"""
    func, commit_url = _validate_common(func, commit_url)
    key = (version or "").strip().lower()
    if key not in VERSION_TO_TARGET:
        raise AdhocError("version 必须为 vulnerable（修复前）或 fixed（修复后）")
    provided = idx is not None
    if provided and (type(idx) is not int or idx < 0):
        raise AdhocError("idx 必须为 >= 0 的整数")
    return AdhocSpec(
        idx=idx if provided else MANUAL_DEFAULT_IDX,
        commit_url=commit_url,
        target=VERSION_TO_TARGET[key],
        func=func,
        provided_idx=provided,
    )


def parse_upload(content: str) -> list[AdhocSpec]:
    """解析上传的 .jsonl；逐行校验字段，错误信息带行号。"""
    if not content or not content.strip():
        raise AdhocError("上传内容为空")
    if len(content) > MAX_UPLOAD_CHARS:
        raise AdhocError(
            f"上传内容过大（{len(content)} 字符），上限 {MAX_UPLOAD_CHARS} 字符"
        )

    specs: list[AdhocSpec] = []
    seen: dict[int, AdhocSpec] = {}
    for line_number, line in enumerate(content.splitlines(), 1):
        if not line.strip():
            continue
        try:
            record = json.loads(line)
        except json.JSONDecodeError as exc:
            raise AdhocError(f"第 {line_number} 行 JSON 格式错误：{exc.msg}") from exc
        if not isinstance(record, dict):
            raise AdhocError(f"第 {line_number} 行必须是 JSON 对象")

        idx = record.get("idx")
        target = record.get("target")
        if type(idx) is not int or idx < 0:
            raise AdhocError(f"第 {line_number} 行 idx 必须是 >= 0 的整数")
        if type(target) is not int or target not in (0, 1):
            raise AdhocError(f"第 {line_number} 行 target 必须是 0 或 1")
        func = record.get("func")
        commit_url = record.get("commit_url")
        if not isinstance(func, str):
            raise AdhocError(f"第 {line_number} 行 func 必须是字符串")
        if not isinstance(commit_url, str):
            raise AdhocError(f"第 {line_number} 行 commit_url 必须是字符串")
        func, commit_url = _validate_common(func, commit_url)

        if idx in seen:
            raise AdhocError(f"第 {line_number} 行 idx={idx} 重复，请先去重")
        spec = AdhocSpec(
            idx=idx,
            commit_url=commit_url,
            target=target,
            func=func,
            provided_idx=True,
        )
        seen[idx] = spec
        specs.append(spec)

    if not specs:
        raise AdhocError("上传内容里没有有效样本行")
    if len(specs) > MAX_UPLOAD_SAMPLES:
        raise AdhocError(
            f"单次上传最多 {MAX_UPLOAD_SAMPLES} 条样本，当前 {len(specs)} 条"
        )
    return specs


def _stamp() -> str:
    return datetime.now().strftime("%Y%m%d-%H%M%S")


def _now() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def _call_progress(hook: ProgressHook | None, stage: str) -> None:
    if hook is not None:
        hook(stage)


def _build_run_entry(
    spec: AdhocSpec,
    agent_outcome: Any,
    snapshot_dir: Path,
    version_role: str,
    requested_commit: str | None,
    resolved_commit: str | None,
    method: str | None,
    locate_status: str,
    locate_path: str | None,
) -> dict[str, Any]:
    return {
        "idx": spec.idx,
        "target": spec.target,
        "version": version_role,
        "commit_url": spec.commit_url,
        "provided_idx": spec.provided_idx,
        "run_id": agent_outcome.run_id,
        "snapshot_dir": display_path(snapshot_dir),
        "method": method,
        "locate_status": locate_status,
        "locate_path": locate_path,
        "requested_commit": requested_commit,
        "resolved_commit": resolved_commit,
        "verdict": agent_outcome.verdict,
        "prediction": agent_outcome.prediction,
        "reason": agent_outcome.reason,
        "evidence": agent_outcome.evidence,
        "steps": agent_outcome.steps,
        "llm_steps": agent_outcome.llm_steps,
        "tool_calls": agent_outcome.tool_calls,
        "timed_out": agent_outcome.timed_out,
        "hit_max_steps": agent_outcome.hit_max_steps,
        "evidence_gate": agent_outcome.gate,
        "is_stub": agent_outcome.is_stub,
    }


def run_adhoc(
    specs: list[AdhocSpec],
    settings: AgentSettings,
    output_dir: Path,
    mode: str = "manual",
    on_progress: ProgressHook | None = None,
) -> dict[str, Any]:
    """逐条准备快照并运行 Agent；单条失败不打断整批。"""
    if not specs:
        raise AdhocError("没有可运行的样本")

    provider, fallback_reason = create_provider(settings)
    repo_settings = RepoServiceSettings.from_env()
    repo_settings.ensure_dirs()
    # 快照按 repo+commit 共享；样本元数据单独放，避免覆盖数据集样本的 meta。
    adhoc_settings = replace(
        repo_settings,
        sample_meta_dir=repo_settings.cache_dir / "meta" / "adhoc",
    )
    adhoc_settings.sample_meta_dir.mkdir(parents=True, exist_ok=True)
    manager = _manager_for(adhoc_settings)

    output_dir = output_dir.resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    trace_path = output_dir / "trace.jsonl"
    trace_path.write_text("", encoding="utf-8")

    limits = ToolLimits(
        max_file_bytes=settings.max_file_bytes,
        max_results=settings.max_results,
        max_read_lines=settings.max_read_lines,
        max_scan_files=settings.max_scan_files,
    )

    run_entries: list[dict[str, Any]] = []
    failures: list[dict[str, Any]] = []
    started = time.perf_counter()

    for spec in specs:
        _call_progress(on_progress, "preparing_snapshot")
        sample = Sample(
            idx=spec.idx,
            commit_url=spec.commit_url,
            target=spec.target,
            func=spec.func,
        )
        outcome = manager.ensure_snapshot(sample)
        if outcome.status != "snapshot_ok" or outcome.snapshot_dir is None:
            message = (
                outcome.error
                or outcome.reason
                or f"快照准备失败（status={outcome.status}）"
            )
            failures.append(
                {
                    "idx": spec.idx,
                    "version": spec.version,
                    "commit_url": spec.commit_url,
                    "status": outcome.status,
                    "error": message,
                }
            )
            continue

        snapshot_dir = outcome.snapshot_dir
        located = locate_function(
            snapshot_dir, spec.func, repo_settings.max_source_file_bytes
        )
        locate_path = located.path if located.status == "found" else None
        guard = SnapshotGuard(snapshot_dir, limits)

        _call_progress(on_progress, "running")
        run_id = f"{_stamp()}-{spec.idx}-{uuid.uuid4().hex[:6]}"
        trace = TraceWriter(run_id, trace_path)
        agent_outcome = run_agent(
            sample=sample,
            guard=guard,
            provider=provider,
            settings=settings,
            trace=trace,
            snapshot_display=display_path(snapshot_dir),
            locate_path=locate_path,
        )
        run_entries.append(
            _build_run_entry(
                spec=spec,
                agent_outcome=agent_outcome,
                snapshot_dir=snapshot_dir,
                version_role=outcome.version_role or spec.version,
                requested_commit=outcome.requested_commit,
                resolved_commit=outcome.resolved_commit,
                method=outcome.method,
                locate_status=located.status,
                locate_path=locate_path,
            )
        )

    result_path = output_dir / "result.jsonl"
    with result_path.open("w", encoding="utf-8", newline="\n") as stream:
        for item in run_entries:
            stream.write(
                json.dumps(
                    {"idx": item["idx"], "prediction": item["prediction"]},
                    ensure_ascii=False,
                )
                + "\n"
            )

    elapsed_seconds = round(time.perf_counter() - started, 2)
    report: dict[str, Any] = {
        "generated_at": _now(),
        "mode": mode,
        "dataset": None,
        "pair": None,
        "provider": {
            "name": provider.name,
            "is_stub": provider.is_stub,
            "model": getattr(provider, "model", None),
            "base_url": getattr(provider, "base_url", None),
            "fallback_reason": fallback_reason,
            "tools": tool_names(),
        },
        "input": {
            "samples": len(specs),
            "all_idx_provided": all(spec.provided_idx for spec in specs),
            "manual_default_idx": MANUAL_DEFAULT_IDX,
        },
        "runs": run_entries,
        "failures": failures,
        "elapsed_seconds": elapsed_seconds,
        "contract_mapping": {
            "result_record": "result.jsonl 每行仅含 idx 与 prediction，符合 result_record.schema.json",
            "trace_event": "trace.jsonl 每行符合 trace_event.schema.json",
            "pair_category": "临时样本不做相邻配对评分；P-x 仍由全量评测链路产出",
        },
        "stub_notice": (
            "本次结果由确定性 stub provider 生成，仅用于验证端到端链路，"
            "不代表真实漏洞检测能力。"
            if provider.is_stub
            else None
        ),
    }
    report_path = output_dir / "report.json"
    report_path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )

    return {
        "output_dir": str(output_dir),
        "result_path": str(result_path),
        "report_path": str(report_path),
        "trace_path": str(trace_path),
        "provider": report["provider"],
        "mode": mode,
        "runs": run_entries,
        "failures": failures,
        "elapsed_seconds": elapsed_seconds,
    }
