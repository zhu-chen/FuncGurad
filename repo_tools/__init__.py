"""只读仓库工具包。

所有工具只能访问 `cache/snapshots/` 下的单个快照目录，禁止联网、禁止写入。
统一返回 `ToolResult`，由 `registry.dispatch` 负责计时与异常封装。
"""
