"""知识库检索工具：让 Agent 主动检索历史 CVE 案例（BM25 单路）。

工具不依赖仓库快照，只读取本地索引；索引缺失时返回可读错误，
由 registry.dispatch 统一转成 ok=false，不中断 Agent 循环。
"""

from __future__ import annotations

from typing import Any

from rag.retriever import KnowledgeIndexError, get_retriever
from repo_tools.base import SnapshotGuard, ToolError

MAX_TOP_K = 10


def run(_guard: SnapshotGuard | None, arguments: dict[str, Any]) -> dict[str, Any]:
    query = str(arguments.get("query") or "").strip()
    if not query:
        raise ToolError("query 不能为空，请给出漏洞现象或代码关键词")

    try:
        top_k = int(arguments.get("top_k") or 5)
    except (TypeError, ValueError):
        top_k = 5
    top_k = max(1, min(top_k, MAX_TOP_K))

    project = str(arguments.get("project") or "").strip() or None
    cwe = str(arguments.get("cwe") or "").strip() or None

    try:
        result = get_retriever().search(
            query, top_k=top_k, project=project, cwe=cwe
        )
    except KnowledgeIndexError as exc:
        raise ToolError(str(exc)) from exc

    return {
        "query": result["query"],
        "top_k": top_k,
        "filters": result["filters"],
        "hits": result["hits"],
        "elapsed_ms": result["elapsed_ms"],
    }
