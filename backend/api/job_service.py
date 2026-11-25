"""HTTP 层任务编排：单条/批量运行在后台线程执行，结果落 cache/runs/<run_id>/。

前端只通过 API 读取状态、报告与轨迹，不在浏览器里做任何业务判断。
任务分两类：
- `dataset`：按测试集 idx 运行，走 `run_service`（评分链路）；
- `manual` / `upload`：临时样本，走 `adhoc_service`（手工输入或上传 jsonl），
  不读测试集，也不参与全量评测。
"""

from __future__ import annotations

import json
import threading
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any

from backend.config import AgentSettings
from backend.providers import create_provider
from backend.services.adhoc_service import AdhocSpec, parse_upload, run_adhoc
from backend.services.run_service import run as run_samples
from backend.services.sample_service import load_dataset
from repo_service.config import display_path


class JobError(RuntimeError):
    """任务创建或读取失败。"""

    def __init__(self, message: str, status_code: int = 400) -> None:
        super().__init__(message)
        self.status_code = status_code


def _now() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


class JobService:
    def __init__(self, settings: AgentSettings, dataset_path: Path) -> None:
        self.settings = settings
        self.dataset_path = Path(dataset_path)
        self.runs_dir = settings.runs_dir
        self.runs_dir.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._runs: dict[str, dict[str, Any]] = {}
        self._batches: dict[str, dict[str, Any]] = {}
        self._provider_info: dict[str, Any] | None = None

    # ---------- provider / 数据集 ----------

    def provider_info(self) -> dict[str, Any]:
        if self._provider_info is None:
            provider, fallback = create_provider(self.settings)
            self._provider_info = {
                "name": provider.name,
                "is_stub": provider.is_stub,
                "model": getattr(provider, "model", None),
                "base_url": getattr(provider, "base_url", None),
                "fallback_reason": fallback,
            }
        return dict(self._provider_info)

    def data_status(self) -> dict[str, Any]:
        """检查首次运行所需数据是否就绪，返回缺失项与可读说明。"""
        if not self.dataset_path.exists():
            missing = display_path(self.dataset_path)
            return {
                "ready": False,
                "missing": [missing],
                "message": (
                    f"缺少测试集 {missing}。测试集属于本地课程数据，不入库；"
                    "请从课程材料获取，或按 docs/handoff/2026-11-22-p2-snapshot.md "
                    "的流程准备。准备后可运行 python -m backend.cli check 自检。"
                ),
            }
        return {"ready": True, "missing": [], "message": ""}

    def dataset_stats(self) -> dict[str, Any]:
        samples, _ = load_dataset(self.dataset_path)
        return {
            "path": display_path(self.dataset_path),
            "samples": len(samples),
            "pairs": len(samples) // 2,
        }

    def list_pairs(self, limit: int = 30) -> list[dict[str, Any]]:
        samples, _ = load_dataset(self.dataset_path)
        pairs: list[dict[str, Any]] = []
        for offset in range(0, len(samples), 2):
            first, second = samples[offset], samples[offset + 1]
            vulnerable, fixed = (
                (first, second) if first.target == 1 else (second, first)
            )
            pairs.append(
                {
                    "pair_no": offset // 2 + 1,
                    "vulnerable_idx": vulnerable.idx,
                    "fixed_idx": fixed.idx,
                    "vulnerable_url": vulnerable.commit_url,
                    "fixed_url": fixed.commit_url,
                    "func_chars": len(vulnerable.func),
                }
            )
            if len(pairs) >= limit:
                break
        return pairs

    # ---------- 单条运行（测试集） ----------

    def create_run(self, idx: int, pair_mode: bool = True) -> dict[str, Any]:
        _, index = load_dataset(self.dataset_path)
        if idx not in index:
            raise JobError(f"idx={idx} 不在测试集中", 400)
        self.provider_info()  # 显式 openai 但缺 key 时在这里失败

        record = self._new_run_record(idx, pair_mode)
        thread = threading.Thread(target=self._execute, args=(record,), daemon=True)
        thread.start()
        return self._public_run(record)

    # ---------- 单条运行（手工样本） ----------

    def create_manual_run(self, spec: AdhocSpec) -> dict[str, Any]:
        self.provider_info()  # 显式 provider 但缺 key 时在这里失败
        record = self._new_run_record(
            spec.idx, pair_mode=False, mode="manual", spec=spec
        )
        thread = threading.Thread(target=self._execute, args=(record,), daemon=True)
        thread.start()
        return self._public_run(record)

    def get_run(self, run_id: str) -> dict[str, Any]:
        with self._lock:
            record = self._runs.get(run_id)
        if record is None:
            raise JobError(f"run_id={run_id} 不存在", 404)
        return self._public_run(record)

    def list_runs(self, limit: int = 20) -> list[dict[str, Any]]:
        with self._lock:
            records = list(self._runs.values())
        records.sort(key=lambda item: item["created_at"], reverse=True)
        return [self._public_run(record) for record in records[:limit]]

    def read_report(self, run_id: str) -> dict[str, Any]:
        record = self._require_record(run_id)
        report_path = Path(record["_output_dir"]) / "report.json"
        if not report_path.exists():
            if record["status"] == "failed":
                raise JobError(
                    f"运行失败，没有报告：{record.get('error')}", 409
                )
            raise JobError(
                f"运行尚未完成（当前状态 {record['status']}），报告暂不可用", 409
            )
        report = json.loads(report_path.read_text(encoding="utf-8"))
        result_records = []
        result_path = Path(record["_output_dir"]) / "result.jsonl"
        if result_path.exists():
            for line in result_path.read_text(encoding="utf-8").splitlines():
                if line.strip():
                    result_records.append(json.loads(line))
        return {
            "report": report,
            "result_records": result_records,
            "run_dir": record["output_dir"],
            "artifacts": ["result.jsonl", "report.json", "trace.jsonl"],
            "is_stub": (report.get("provider") or {}).get("is_stub", False),
        }

    def read_trace(self, run_id: str, after: int = 0) -> dict[str, Any]:
        """按文件顺序返回轨迹；after 是已收到的事件条数，不是 seq。"""
        record = self._require_record(run_id)
        trace_path = Path(record["_output_dir"]) / "trace.jsonl"
        events: list[dict[str, Any]] = []
        if trace_path.exists():
            for line in trace_path.read_text(encoding="utf-8").splitlines():
                if not line.strip():
                    continue
                try:
                    events.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
        if after < 0:
            after = 0
        return {
            "run_id": run_id,
            "status": record["status"],
            "events": events[after:],
            "next": len(events),
            "is_stub": record.get("is_stub", self.provider_info()["is_stub"]),
        }

    # ---------- 批量 ----------

    def create_batch(self, idx_list: list[int], pair_mode: bool = True) -> dict[str, Any]:
        if not idx_list:
            raise JobError("idx_list 不能为空", 400)
        _, index = load_dataset(self.dataset_path)
        deduped: list[int] = []
        for idx in idx_list:
            if idx not in index:
                raise JobError(f"idx={idx} 不在测试集中", 400)
            if idx not in deduped:
                deduped.append(idx)
        self.provider_info()

        batch_id = self._new_batch_id()
        records = [
            self._new_run_record(idx, pair_mode, batch_id=batch_id)
            for idx in deduped
        ]
        return self._start_batch(batch_id, records, deduped)

    def create_upload_batch(self, content: str) -> dict[str, Any]:
        """上传 .jsonl：逐条转成临时样本，后台串行执行。"""
        specs = parse_upload(content)  # 解析失败抛 AdhocError，由 app 层转 400
        self.provider_info()

        batch_id = self._new_batch_id()
        records = [
            self._new_run_record(
                spec.idx, pair_mode=False, batch_id=batch_id, mode="upload", spec=spec
            )
            for spec in specs
        ]
        batch = self._register_batch(batch_id, records, [spec.idx for spec in specs])
        return self._public_batch(batch)

    def get_batch(self, batch_id: str) -> dict[str, Any]:
        with self._lock:
            batch = self._batches.get(batch_id)
        if batch is None:
            raise JobError(f"batch_id={batch_id} 不存在", 404)
        return self._public_batch(batch)

    # ---------- 内部 ----------

    @staticmethod
    def _new_batch_id() -> str:
        return (
            f"batch-{datetime.now().strftime('%Y%m%d-%H%M%S')}-"
            f"{uuid.uuid4().hex[:6]}"
        )

    def _start_batch(
        self,
        batch_id: str,
        records: list[dict[str, Any]],
        idx_list: list[int],
    ) -> dict[str, Any]:
        batch = self._register_batch(batch_id, records, idx_list)
        return self._public_batch(batch)

    def _register_batch(
        self,
        batch_id: str,
        records: list[dict[str, Any]],
        idx_list: list[int],
    ) -> dict[str, Any]:
        batch = {
            "batch_id": batch_id,
            "status": "queued",
            "created_at": _now(),
            "started_at": None,
            "ended_at": None,
            "idx_list": idx_list,
            "items": records,
        }
        with self._lock:
            self._batches[batch_id] = batch
        thread = threading.Thread(target=self._run_batch, args=(batch,), daemon=True)
        thread.start()
        return batch

    def _new_run_record(
        self,
        idx: int,
        pair_mode: bool,
        batch_id: str | None = None,
        mode: str = "dataset",
        spec: AdhocSpec | None = None,
    ) -> dict[str, Any]:
        run_id = (
            f"{datetime.now().strftime('%Y%m%d-%H%M%S')}-{idx}-"
            f"{uuid.uuid4().hex[:6]}"
        )
        output_dir = (self.runs_dir / run_id).resolve()
        output_dir.mkdir(parents=True, exist_ok=True)
        (output_dir / "trace.jsonl").touch()

        record: dict[str, Any] = {
            "run_id": run_id,
            "requested_idx": idx,
            "pair_mode": pair_mode,
            "batch_id": batch_id,
            "mode": mode,
            "version": spec.version if spec else None,
            "commit_url": spec.commit_url if spec else None,
            "provided_idx": spec.provided_idx if spec else True,
            "stage": None,
            "status": "queued",
            "created_at": _now(),
            "started_at": None,
            "ended_at": None,
            "error": None,
            "output_dir": display_path(output_dir),
            "_output_dir": str(output_dir),
            "_spec": spec,
            "is_stub": self.provider_info()["is_stub"],
        }
        with self._lock:
            self._runs[run_id] = record
        return record

    def _execute(self, record: dict[str, Any]) -> None:
        if record.get("_spec") is not None:
            self._execute_adhoc(record)
            return
        self._execute_dataset(record)

    def _execute_dataset(self, record: dict[str, Any]) -> None:
        with self._lock:
            record["status"] = "running"
            record["stage"] = "running"
            record["started_at"] = _now()
        try:
            payload = run_samples(
                [record["requested_idx"]],
                self.dataset_path,
                self.settings,
                output_dir=Path(record["_output_dir"]),
                pair_mode=record["pair_mode"],
            )
            with self._lock:
                record["status"] = "done"
                record["stage"] = "done"
                record["ended_at"] = _now()
                record["pair"] = payload.get("pair")
                record["provider"] = payload.get("provider")
                record["is_stub"] = (payload.get("provider") or {}).get(
                    "is_stub", record.get("is_stub", False)
                )
        except Exception as exc:  # 单条失败不拖垮批量
            with self._lock:
                record["status"] = "failed"
                record["stage"] = "failed"
                record["ended_at"] = _now()
                record["error"] = f"{type(exc).__name__}: {exc}"

    def _execute_adhoc(self, record: dict[str, Any]) -> None:
        """临时样本：先准备快照再跑 Agent，阶段写进 status 供前端展示。"""

        def on_progress(stage: str) -> None:
            with self._lock:
                record["status"] = stage
                record["stage"] = stage

        with self._lock:
            record["status"] = "queued"
            record["stage"] = "queued"
            record["started_at"] = _now()
        try:
            payload = run_adhoc(
                [record["_spec"]],
                self.settings,
                output_dir=Path(record["_output_dir"]),
                mode=record["mode"],
                on_progress=on_progress,
            )
            failures = payload.get("failures") or []
            if failures:
                raise JobError(failures[0]["error"], 422)
            with self._lock:
                record["status"] = "done"
                record["stage"] = "done"
                record["ended_at"] = _now()
                record["provider"] = payload.get("provider")
                record["is_stub"] = (payload.get("provider") or {}).get(
                    "is_stub", record.get("is_stub", False)
                )
        except Exception as exc:  # 快照准备或 Agent 失败只影响本条
            with self._lock:
                record["status"] = "failed"
                record["stage"] = "failed"
                record["ended_at"] = _now()
                record["error"] = (
                    str(exc) if isinstance(exc, JobError) else f"{type(exc).__name__}: {exc}"
                )

    def _run_batch(self, batch: dict[str, Any]) -> None:
        with self._lock:
            batch["status"] = "running"
            batch["started_at"] = _now()
        for record in batch["items"]:
            self._execute(record)

        statuses = [item["status"] for item in batch["items"]]
        with self._lock:
            if all(status == "done" for status in statuses):
                batch["status"] = "done"
            elif all(status == "failed" for status in statuses):
                batch["status"] = "failed"
            else:
                batch["status"] = "partial_failed"
            batch["ended_at"] = _now()

    def _require_record(self, run_id: str) -> dict[str, Any]:
        with self._lock:
            record = self._runs.get(run_id)
        if record is None:
            raise JobError(f"run_id={run_id} 不存在", 404)
        return record

    def _public_run(self, record: dict[str, Any]) -> dict[str, Any]:
        data = {
            key: value
            for key, value in record.items()
            if not key.startswith("_")
        }
        output_dir = Path(record["_output_dir"])
        data["report_ready"] = (output_dir / "report.json").exists()
        pair = record.get("pair") or {}
        data["pair_category"] = pair.get("category")
        return data

    def _public_batch(self, batch: dict[str, Any]) -> dict[str, Any]:
        items = [self._public_run(item) for item in batch["items"]]
        done = sum(1 for item in items if item["status"] == "done")
        failed = sum(1 for item in items if item["status"] == "failed")
        return {
            "batch_id": batch["batch_id"],
            "status": batch["status"],
            "created_at": batch["created_at"],
            "started_at": batch["started_at"],
            "ended_at": batch["ended_at"],
            "idx_list": batch["idx_list"],
            "total": len(items),
            "done": done,
            "failed": failed,
            "items": items,
        }
