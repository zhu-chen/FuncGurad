"""全量评测驱动：按仓库分组准备快照、逐样本运行 Agent、汇总结果并评分。

设计目标：
- 断点续跑：每跑完一个样本就原子写入 predictions.jsonl / status.jsonl。
- 控制磁盘：同一仓库的样本跑完后删除该仓库的快照与 git 克隆。
- 逐样本隔离：单个样本失败只记为 fallback（prediction=0），不中断全量。

用法（在仓库根目录）：
    python scripts/run_full_eval.py
    python scripts/run_full_eval.py --cache cache/full-eval --only-repos ImageMagick__ImageMagick6

缺省缓存目录是仓库内相对路径 cache/full-eval，可用环境变量
FUNCGUARD_FULL_EVAL_CACHE 或 --cache 覆盖。
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import time
import traceback
from collections import Counter, OrderedDict
from datetime import datetime
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_DATASET = ROOT / "dataset" / "test.jsonl"

# 以脚本方式运行时，sys.path[0] 是 scripts/，需要显式加入仓库根目录。
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def _now() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def _safe_name(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]", "_", value)


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    records: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            try:
                records.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return records


def _write_jsonl(path: Path, records: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with tmp.open("w", encoding="utf-8", newline="\n") as stream:
        for record in records:
            stream.write(json.dumps(record, ensure_ascii=False) + "\n")
    os.replace(tmp, path)


def _snapshot_ready(meta_path: Path) -> bool:
    """元数据存在且 snapshot_dir 仍然存在时，才认为样本可运行。"""
    try:
        payload = json.loads(meta_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return False
    raw = str(payload.get("snapshot_dir") or "").strip()
    if not raw:
        return False
    snapshot = Path(raw)
    if not snapshot.is_absolute():
        snapshot = ROOT / snapshot
    return snapshot.is_dir()


def _make_synthetic_snapshot(sample: Any, cache: Path) -> Path:
    """commit 不可达时的兜底：只把数据集提供的函数文本写成一个快照。

    这不是真实仓库快照，只用于让 Agent 仍能基于函数文本给出可复核结论；
    状态记录会带 synthetic_snapshot=True，报告中需单独说明覆盖率。
    """
    snapshot = cache / "snapshots" / f"_synthetic__{sample.idx}"
    snapshot.mkdir(parents=True, exist_ok=True)
    (snapshot / "func.c").write_text(
        "/* synthetic fallback: real commit was unreachable; "
        "only the dataset function text is available */\n"
        + sample.func.strip()
        + "\n",
        encoding="utf-8",
    )
    meta = {
        "idx": sample.idx,
        "commit_url": sample.commit_url,
        "requested_commit": None,
        "resolved_commit": None,
        "version_role": "vulnerable" if sample.target == 1 else "fixed",
        "snapshot_dir": str(snapshot),
        "method": "synthetic-fallback",
        "locate_status": "found",
        "locate_path": "func.c",
        "has_git_dir": False,
        "note": "real commit unreachable; snapshot contains only dataset func text",
        "created_at": _now(),
    }
    meta_path = cache / "meta" / "samples" / f"{sample.idx}.json"
    meta_path.parent.mkdir(parents=True, exist_ok=True)
    meta_path.write_text(
        json.dumps(meta, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return meta_path


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="FuncGurad 全量评测")
    parser.add_argument("--dataset", type=Path, default=DEFAULT_DATASET)
    parser.add_argument(
        "--cache",
        type=Path,
        default=Path(
            os.getenv("FUNCGUARD_FULL_EVAL_CACHE", str(ROOT / "cache" / "full-eval"))
        ),
        help="快照/元数据/运行产物目录；默认 cache/full-eval",
    )
    parser.add_argument("--workers", type=int, default=4, help="快照准备并发数")
    parser.add_argument(
        "--prepare-timeout",
        type=int,
        default=7200,
        help="单仓库快照准备超时（秒）",
    )
    parser.add_argument("--max-steps", type=int, default=10)
    parser.add_argument("--timeout", type=int, default=900, help="单样本总超时（秒）")
    parser.add_argument(
        "--request-timeout", type=int, default=180, help="单次模型请求超时（秒）"
    )
    parser.add_argument(
        "--reasoning-effort",
        choices=("low", "high", "max"),
        default="low",
    )
    parser.add_argument(
        "--only-repos",
        default="",
        help="只跑指定 repo_key，逗号分隔；用于小范围试跑",
    )
    parser.add_argument(
        "--keep-snapshots",
        action="store_true",
        help="跑完不删除该仓库快照（默认删除以控制磁盘）",
    )
    parser.add_argument(
        "--allow-stub",
        action="store_true",
        help="允许使用 stub provider（默认遇到 stub 直接退出）",
    )
    parser.add_argument(
        "--synthetic-fallback",
        action="store_true",
        help="commit 不可达时，用数据集函数文本生成合成快照并继续评测（报告中标注）",
    )
    args = parser.parse_args(argv)

    dataset = args.dataset.resolve()
    cache = args.cache.resolve()
    out = cache / "eval"
    (out / "logs").mkdir(parents=True, exist_ok=True)
    (out / "runs").mkdir(parents=True, exist_ok=True)

    # 必须在导入 backend.config 前设置，保证 from_env() 使用评测缓存目录。
    os.environ["FUNCGUARD_CACHE_DIR"] = str(cache)
    os.environ["FUNCGUARD_AGENT_MAX_STEPS"] = str(args.max_steps)
    os.environ["FUNCGUARD_AGENT_TIMEOUT"] = str(args.timeout)
    os.environ["FUNCGUARD_LLM_TIMEOUT"] = str(args.request_timeout)
    os.environ["FUNCGUARD_LLM_REASONING_EFFORT"] = args.reasoning_effort
    os.environ.setdefault("FUNCGUARD_LLM_STREAM", "1")
    os.environ.setdefault("FUNCGUARD_AGENT_MAX_TOOL_CONTENT", "16000")

    from backend.config import AgentSettings
    from backend.providers import create_provider
    from backend.services.run_service import run as run_samples
    from bench.dataset import load_samples
    from rag.retriever import KnowledgeIndexError, get_retriever
    from repo_service.urls import classify_commit_url

    log_path = out / "run.log"

    def log(message: str) -> None:
        line = f"[{_now()}] {message}"
        print(line, flush=True)
        with log_path.open("a", encoding="utf-8") as stream:
            stream.write(line + "\n")

    samples = load_samples(dataset)
    idx_order = [sample.idx for sample in samples]
    settings = AgentSettings.from_env()
    provider, fallback_reason = create_provider(settings)
    if provider.is_stub and not args.allow_stub:
        log("!! provider 回退到 stub，拒绝执行全量评测（可用 --allow-stub 覆盖）")
        return 2

    try:
        index = get_retriever()
        log(f"knowledge index: {len(index.cards)} cards")
    except KnowledgeIndexError as exc:
        log(f"!! knowledge index 不可用，search_knowledge 会失败: {exc}")

    log(
        "provider="
        f"{provider.name} model={getattr(provider, 'model', None)} "
        f"base_url={getattr(provider, 'base_url', None)} "
        f"stub={provider.is_stub} fallback={fallback_reason}"
    )
    log(
        f"settings max_steps={settings.max_steps} timeout={settings.timeout_seconds}s "
        f"request_timeout={settings.request_timeout_seconds}s "
        f"max_tokens={settings.max_tokens} stream={settings.stream} "
        f"effort={settings.reasoning_effort} retries={settings.retries}"
    )
    log(f"dataset={dataset} samples={len(samples)} cache={cache}")

    def effective_repo(url: str) -> str:
        try:
            ref = classify_commit_url(url)
        except ValueError:
            return "unknown"
        if ref.repo_key != "unknown":
            return ref.repo_key
        if ref.mirror:
            return ref.mirror.replace("/", "__")
        return "unknown"

    groups: "OrderedDict[str, list[Any]]" = OrderedDict()
    for sample in samples:
        groups.setdefault(effective_repo(sample.commit_url), []).append(sample)

    only = {item.strip() for item in args.only_repos.split(",") if item.strip()}
    if only:
        groups = OrderedDict((key, value) for key, value in groups.items() if key in only)
        log(f"only repos: {sorted(groups)}")

    # 小仓库先跑，unknown/qemu 放最后，TensorFlow 这类大仓库自然靠后。
    ordered = sorted(groups.items(), key=lambda item: (item[0] == "unknown", len(item[1])))

    predictions_path = out / "predictions.jsonl"
    status_path = out / "status.jsonl"
    predictions: dict[int, int] = {}
    for record in _read_jsonl(predictions_path):
        try:
            predictions[int(record["idx"])] = int(record["prediction"])
        except (KeyError, TypeError, ValueError):
            continue
    status_map: dict[int, dict[str, Any]] = {}
    for record in _read_jsonl(status_path):
        try:
            status_map[int(record["idx"])] = record
        except (KeyError, TypeError, ValueError):
            continue

    def save_state() -> None:
        _write_jsonl(
            predictions_path,
            [
                {"idx": idx, "prediction": predictions[idx]}
                for idx in idx_order
                if idx in predictions
            ],
        )
        _write_jsonl(
            status_path,
            [status_map[idx] for idx in idx_order if idx in status_map],
        )

    def is_done(idx: int) -> bool:
        record = status_map.get(idx)
        return bool(record and record.get("status") == "done")

    def record_fallback(sample: Any, repo: str, status: str, error: str) -> None:
        predictions[sample.idx] = 0
        status_map[sample.idx] = {
            "idx": sample.idx,
            "repo": repo,
            "status": status,
            "prediction": 0,
            "verdict": "benign",
            "reason": "fallback: " + error[:300],
            "error": error[:1000],
            "recorded_at": _now(),
        }

    def prepare(repo: str, idxs: list[int]) -> int:
        safe = _safe_name(repo)
        manifest = out / "logs" / f"manifest-{safe}.jsonl"
        log_file = out / "logs" / f"prepare-{safe}.log"
        command = [
            sys.executable,
            "-m",
            "repo_service.prepare",
            "--dataset",
            str(dataset),
            "--workers",
            str(args.workers),
            "--manifest",
            str(manifest),
        ]
        for idx in idxs:
            command.extend(["--only-idx", str(idx)])
        log(f"prepare {repo}: {len(idxs)} samples -> {log_file}")
        started = time.monotonic()
        with log_file.open("w", encoding="utf-8") as stream:
            process = subprocess.Popen(
                command,
                cwd=str(ROOT),
                env=os.environ.copy(),
                stdout=stream,
                stderr=subprocess.STDOUT,
            )
            try:
                returncode = process.wait(timeout=args.prepare_timeout)
            except subprocess.TimeoutExpired:
                # Windows 下必须杀整棵进程树，否则 git 子进程会继续下载。
                if os.name == "nt":
                    subprocess.run(
                        ["taskkill", "/F", "/T", "/PID", str(process.pid)],
                        capture_output=True,
                    )
                else:
                    process.kill()
                process.wait()
                log(
                    f"prepare {repo}: TIMEOUT after {args.prepare_timeout}s; "
                    "will run whatever metadata exists"
                )
                return 124
        log(
            f"prepare {repo}: exit={returncode} "
            f"elapsed={time.monotonic() - started:.1f}s"
        )
        return returncode

    def cleanup(repo: str) -> None:
        if args.keep_snapshots:
            return
        for path in (cache / "snapshots").glob(f"{repo}__*"):
            shutil.rmtree(path, ignore_errors=True)
        for suffix in ("", "__shallow"):
            shutil.rmtree(cache / "git" / f"{repo}{suffix}", ignore_errors=True)
        for path in (cache / "tmp").glob(f"{repo}__*"):
            try:
                path.unlink()
            except OSError:
                pass

    started_all = time.monotonic()
    total_groups = len(ordered)
    for group_number, (repo, group) in enumerate(ordered, 1):
        pending = [sample for sample in group if not is_done(sample.idx)]
        if not pending:
            log(f"[{group_number}/{total_groups}] {repo}: already complete")
            continue
        log(
            f"[{group_number}/{total_groups}] {repo}: "
            f"{len(pending)} pending / {len(group)} samples"
        )

        prepare(repo, [sample.idx for sample in pending])

        synthetic_idxs: set[int] = set()
        runnable = []
        for sample in pending:
            meta = cache / "meta" / "samples" / f"{sample.idx}.json"
            if meta.exists() and _snapshot_ready(meta):
                runnable.append(sample)
            elif args.synthetic_fallback:
                _make_synthetic_snapshot(sample, cache)
                synthetic_idxs.add(sample.idx)
                runnable.append(sample)
                log(
                    f"  idx={sample.idx}: real snapshot unavailable, "
                    "using synthetic function-text snapshot"
                )
            else:
                record_fallback(sample, repo, "no_snapshot", "缺少样本快照元数据")
                log(f"  idx={sample.idx}: no snapshot metadata, fallback benign")
                save_state()

        for position, sample in enumerate(runnable, 1):
            run_dir = out / "runs" / _safe_name(repo) / f"{sample.idx}"
            started = time.monotonic()
            try:
                payload = run_samples(
                    [sample.idx],
                    dataset,
                    settings,
                    output_dir=run_dir,
                    pair_mode=False,
                )
                item = payload["runs"][0]
                if payload["provider"].get("is_stub") and not args.allow_stub:
                    raise RuntimeError("provider 回退到 stub，停止评测")
                predictions[sample.idx] = int(item["prediction"])
                status_map[sample.idx] = {
                    "idx": sample.idx,
                    "repo": repo,
                    "status": "done",
                    "prediction": int(item["prediction"]),
                    "verdict": item.get("verdict"),
                    "reason": str(item.get("reason") or "")[:1000],
                    "steps": item.get("steps"),
                    "llm_steps": item.get("llm_steps"),
                    "tool_calls": len(item.get("tool_calls") or []),
                    "timed_out": item.get("timed_out"),
                    "hit_max_steps": item.get("hit_max_steps"),
                    "evidence_gate": item.get("evidence_gate"),
                    "is_stub": item.get("is_stub"),
                    "synthetic_snapshot": sample.idx in synthetic_idxs,
                    "elapsed_seconds": round(time.monotonic() - started, 2),
                    "recorded_at": _now(),
                }
                log(
                    f"  [{position}/{len(runnable)}] idx={sample.idx} "
                    f"pred={item['prediction']} verdict={item['verdict']} "
                    f"steps={item.get('steps')} "
                    f"tools={len(item.get('tool_calls') or [])} "
                    f"elapsed={time.monotonic() - started:.1f}s"
                )
            except Exception as exc:
                error = f"{type(exc).__name__}: {exc}"
                record_fallback(sample, repo, "error", error)
                error_log = out / "logs" / f"error-{sample.idx}.log"
                error_log.write_text(traceback.format_exc(), encoding="utf-8")
                log(f"  [{position}/{len(runnable)}] idx={sample.idx} ERROR {error}")
            save_state()

        cleanup(repo)
        log(f"cleanup {repo}: snapshots/git removed")

    for sample in samples:
        if sample.idx not in predictions:
            record_fallback(sample, "missing", "missing", "评测结束时仍无预测")
    save_state()

    result_path = out / "full-result.jsonl"
    _write_jsonl(
        result_path,
        [{"idx": sample.idx, "prediction": predictions[sample.idx]} for sample in samples],
    )

    score_json = out / "eval-report.json"
    score = subprocess.run(
        [
            sys.executable,
            "-m",
            "bench.score",
            "--dataset",
            str(dataset),
            "--result",
            str(result_path),
            "--json-out",
            str(score_json),
        ],
        cwd=str(ROOT),
        env=os.environ.copy(),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    (out / "score.txt").write_text(
        (score.stdout or "") + (score.stderr or ""), encoding="utf-8"
    )
    log("=== bench.score ===")
    for line in (score.stdout or "").strip().splitlines():
        log(line)

    official = subprocess.run(
        [
            sys.executable,
            str(ROOT / "dataset" / "evaluation.py"),
            "--dataset",
            str(dataset),
            "--result",
            str(result_path),
        ],
        cwd=str(ROOT),
        env=os.environ.copy(),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    (out / "official-evaluation.txt").write_text(
        (official.stdout or "") + (official.stderr or ""), encoding="utf-8"
    )
    log("=== official evaluation.py ===")
    for line in (official.stdout or "").strip().splitlines():
        log(line)

    report: dict[str, Any] = {}
    if score_json.exists():
        report = json.loads(score_json.read_text(encoding="utf-8"))

    status_counts = Counter(
        record.get("status") for record in status_map.values()
    )
    summary = {
        "generated_at": _now(),
        "dataset": str(dataset),
        "result": str(result_path),
        "provider": {
            "name": provider.name,
            "model": getattr(provider, "model", None),
            "base_url": getattr(provider, "base_url", None),
            "is_stub": provider.is_stub,
        },
        "settings": {
            "max_steps": settings.max_steps,
            "timeout_seconds": settings.timeout_seconds,
            "request_timeout_seconds": settings.request_timeout_seconds,
            "max_tokens": settings.max_tokens,
            "stream": settings.stream,
            "reasoning_effort": settings.reasoning_effort,
            "retries": settings.retries,
            "workers": args.workers,
        },
        "samples": len(samples),
        "predictions": len(predictions),
        "status_counts": dict(status_counts),
        "synthetic_snapshots": sum(
            1 for record in status_map.values() if record.get("synthetic_snapshot")
        ),
        "elapsed_seconds": round(time.monotonic() - started_all, 1),
        "score": report,
    }
    (out / "summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    log(f"summary -> {out / 'summary.json'}")
    log(f"result  -> {result_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())