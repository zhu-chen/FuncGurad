"""P4 Agent 命令行入口。

用法（在仓库根目录执行）：
    python -m backend.cli run --idx 194989
    python -m backend.cli run --idx 194989 --single
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Any

from backend.config import AgentSettings
from backend.providers import create_provider
from backend.providers.base import ProviderError
from backend.services.run_service import run as run_samples
from backend.services.sample_service import SampleServiceError, load_dataset
from repo_service.config import REPO_ROOT, display_path
from repo_service.config import Settings as RepoServiceSettings
from repo_tools.base import ToolError


def _default_dataset() -> Path:
    return REPO_ROOT / "cache" / "dataset" / "test.jsonl"


def _describe_tool(call: dict[str, Any]) -> str:
    summary = call.get("summary") or {}
    name = call.get("name", "?")
    status = "ok" if call.get("ok") else "error"
    elapsed = call.get("elapsed_ms", 0)

    if name == "find_files":
        detail = f"{summary.get('total_matches', 0)} 个文件"
        paths = summary.get("paths") or []
        if paths:
            detail += f"（{', '.join(paths[:2])}）"
    elif name == "grep_content":
        detail = f"{summary.get('total_matches', 0)} 处命中"
        hits = summary.get("hits") or []
        if hits:
            detail += "（" + ", ".join(
                f"{hit['path']}:{hit['line']}" for hit in hits[:2]
            ) + "）"
    elif name == "read_file":
        detail = (
            f"{summary.get('path')}:{summary.get('start_line')}-"
            f"{summary.get('end_line')} / 共 {summary.get('total_lines')} 行"
        )
    else:
        detail = ""

    if summary.get("error"):
        detail = f"{detail} error={summary['error']}".strip()
    return f"{name}[{status}, {elapsed}ms] {detail}".strip()


def _print_summary(payload: dict[str, Any]) -> None:
    provider = payload["provider"]
    print("=== P4 Agent 运行摘要 ===")
    print(
        f"provider: {provider['name']} (is_stub={provider['is_stub']}, "
        f"model={provider.get('model')}, base_url={provider.get('base_url')})"
    )
    if provider.get("fallback_reason"):
        print(f"回退原因: {provider['fallback_reason']}")
    print(f"工具: {', '.join(provider['tools'])}")
    print()

    runs = payload["runs"]
    for order, item in enumerate(runs, 1):
        print(
            f"[{order}/{len(runs)}] idx={item['idx']} target={item['target']} "
            f"run={item['run_id']}"
        )
        print(
            f"  步数: {item['steps']}（LLM 步 {item['llm_steps']}，"
            f"工具调用 {len(item['tool_calls'])}）"
        )
        if item["tool_calls"]:
            print("  工具轨迹:")
            for call in item["tool_calls"]:
                print(f"    - {_describe_tool(call)}")
        print(f"  结论: {item['verdict']} (prediction={item['prediction']})")
        print(f"  理由: {item['reason']}")
        gate = item.get("evidence_gate") or {}
        print(
            f"  证据门控: {'已触发' if gate.get('applied') else '未触发'} | "
            f"{gate.get('note', '')}"
        )
        if item.get("timed_out"):
            print("  注意: 触发了超时保护")
        if item.get("hit_max_steps"):
            print("  注意: 触发了最大步数保护")
        print()

    pair = payload.get("pair")
    if pair:
        print(
            f"配对: ({pair['vulnerable_idx']}, {pair['fixed_idx']}) → "
            f"{pair['category']}（{pair['meaning']}）"
        )
    else:
        print("配对: 未计算（缺少配对样本的预测）")

    if provider.get("is_stub"):
        print(
            "注意: 本次由 stub provider 生成，仅验证链路，不代表真实检测能力。"
        )

    print(f"输出目录: {payload['output_dir']}")
    print("  result.jsonl / report.json / trace.jsonl")


def _check_data() -> int:
    """检查首次运行所需数据是否就绪。"""
    repo_settings = RepoServiceSettings.from_env()
    dataset_path = _default_dataset()
    problems = 0

    if dataset_path.exists():
        try:
            samples, _ = load_dataset(dataset_path)
            print(f"[OK] 测试集: {display_path(dataset_path)}（{len(samples)} 条样本）")
        except SampleServiceError as exc:
            problems += 1
            print(f"[损坏] 测试集: {exc}")
    else:
        problems += 1
        print(f"[缺失] 测试集: {display_path(dataset_path)}")
        print("       从课程材料获取，或按 docs/handoff/2026-11-22-p2-snapshot.md 准备。")

    meta_dir = repo_settings.sample_meta_dir
    meta_files = list(meta_dir.glob("*.json")) if meta_dir.is_dir() else []
    if meta_files:
        print(f"[OK] 样本快照元数据: {display_path(meta_dir)}（{len(meta_files)} 个）")
    else:
        problems += 1
        print(f"[缺失] 样本快照元数据: {display_path(meta_dir)}/*.json")
        print("       运行 python -m repo_service.prepare --only-idx <idx> 生成。")

    snapshots_dir = repo_settings.snapshots_dir
    snapshot_count = (
        sum(1 for item in snapshots_dir.iterdir() if item.is_dir())
        if snapshots_dir.is_dir()
        else 0
    )
    if snapshot_count:
        print(f"[OK] 快照目录: {display_path(snapshots_dir)}（{snapshot_count} 个）")
    else:
        problems += 1
        print(f"[缺失] 快照目录: {display_path(snapshots_dir)}（为空或不存在）")

    try:
        provider, fallback = create_provider(AgentSettings.from_env())
        print(
            f"[OK] provider: {provider.name}（is_stub={provider.is_stub}, "
            f"model={getattr(provider, 'model', None)}, "
            f"base_url={getattr(provider, 'base_url', None)}）"
        )
        if fallback:
            print(f"       回退原因: {fallback}")
        if not provider.is_stub:
            print("       真实模型已接入（key 来自环境变量或 .env.local）。")
    except ProviderError as exc:
        problems += 1
        print(f"[错误] provider: {exc}")

    if problems:
        print(f"结论: 缺少 {problems} 类数据，服务可启动但运行会返回 503。")
        return 2
    print("结论: 数据就绪，可以启动服务。")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m backend.cli", description="FuncGurad P4 Agent 运行入口"
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    run_parser = subparsers.add_parser("run", help="对指定样本运行 Agent")
    run_parser.add_argument(
        "--idx",
        type=int,
        action="append",
        required=True,
        help="样本 idx，可重复传入",
    )
    run_parser.add_argument(
        "--dataset",
        type=Path,
        default=None,
        help="测试集路径，默认 cache/dataset/test.jsonl",
    )
    run_parser.add_argument(
        "--out",
        type=Path,
        default=None,
        help="输出目录，默认 cache/runs/<时间戳>-idx-<idx>",
    )
    run_parser.add_argument(
        "--single",
        action="store_true",
        help="只跑指定样本，不自动补齐相邻配对样本",
    )
    subparsers.add_parser("check", help="检查首次运行所需数据是否就绪")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    if args.command == "check":
        return _check_data()

    settings = AgentSettings.from_env()
    dataset_path = args.dataset or _default_dataset()

    try:
        payload = run_samples(
            requested_idx=args.idx,
            dataset_path=dataset_path,
            settings=settings,
            output_dir=args.out,
            pair_mode=not args.single,
        )
    except (SampleServiceError, ProviderError, ToolError) as exc:
        print(f"[ERROR] {exc}", file=sys.stderr)
        return 2

    _print_summary(payload)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
