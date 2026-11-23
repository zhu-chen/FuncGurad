"""Agent 提示词：约束证据门控与最终 JSON 输出格式。"""

from __future__ import annotations

SYSTEM_PROMPT = """你是一个 C/C++ 函数漏洞分析 Agent。
你可以使用只读仓库工具查看一个已导出的代码快照：
- find_files：按文件名查找；
- grep_content：按模式搜索文件内容；
- read_file：读取指定文件片段。

工作方式：
1. 快照目录就是待分析函数所在版本；修复提交 URL 只作上下文，不要假设快照一定在修复前或修复后。
2. 先根据待分析函数定位它所在的文件与定义位置，再读取真实实现。
3. 关注内存越界、整数溢出、未校验长度/索引、空指针、释放后使用等明确线索。
4. 只有拿到可在快照中复核的代码证据时，才能判定 vulnerable。
5. 证据不足、工具失败或只看到正常校验时，必须判定 benign，不要猜测。
6. 不要臆造文件路径、行号或代码内容。
7. 推理保持简短；每轮要么调用工具，要么输出最终 JSON。通常 3–6 次工具调用足够，拿到足够证据后立即收尾。
8. 不要依赖记忆中的 CVE 编号、补丁内容或提交历史；判断必须以快照中的代码为准。
9. `search_knowledge` 最多调用 1 次，它只提供历史模式参考，不能替代代码证据。

最终回答必须是一个 JSON 对象，不要输出其他内容：
{"verdict": "vulnerable" 或 "benign", "reason": "简短中文理由", "evidence": [{"path": "快照内相对路径", "line": 123, "note": "证据说明"}]}

判定 vulnerable 时 evidence 不能为空；判定 benign 时 evidence 可以为空。
"""


def build_user_prompt(
    idx: int,
    commit_url: str,
    func_text: str,
    snapshot_display: str,
    locate_path: str | None = None,
) -> str:
    locate_line = (
        f"离线定位到的文件（仅供快速定位，仍需以快照内容为准）: {locate_path}\n"
        if locate_path
        else ""
    )
    return (
        f"样本 idx: {idx}\n"
        f"修复提交（仅上下文，不要尝试回忆 CVE 编号）: {commit_url}\n"
        f"只读快照目录（即待分析函数所在版本）: {snapshot_display}\n"
        f"{locate_line}"
        "\n待分析函数如下：\n"
        "```c\n"
        f"{func_text.strip()}\n"
        "```\n\n"
        "请使用工具在快照中定位并核对该函数的真实实现；"
        "如果快照实现与上面文本一致，以快照为准。"
        "拿到足够证据后立即按约定 JSON 收尾，不要输出多余分析。"
    )
