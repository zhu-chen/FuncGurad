# Agent 检索流程与步数标记

2026.11.26 · WillLin

- `fix/hit-max-steps` 修复运行摘要。进入第 `max_steps` 步即标记 `hit_max_steps=true`，该步禁用工具并要求最终 JSON。最后一步正常收尾或发生错误均使用同一口径，提前结束的运行保留 `false`。
- `feat/retrieval-first-flow` 默认使用 `retrieval_first`。有可用知识索引时，提示词引导模型先检索课程历史案例，写出初判与待核实点，证据不足时按需读仓。索引缺失时从待分析函数开始初判。工具与收尾时机由模型决定。
- `FUNCGUARD_AGENT_PROMPT_PROFILE=legacy` 选择 11 月 24 日评测所用的原系统和用户提示词，两个文本均已校验。运行前仍按本地索引加载结果确定是否提供检索工具。

CLI 摘要、API 返回的报告和浏览器「report.json 原始内容」均提供逐样本 `runs[].flow`。轨迹的 `run_end.payload.flow` 同样包含提示词配置、知识索引可用性、检索和读仓次数、首个工具，以及先后顺序。次数按实际执行的调用记录计算，包含返回错误的调用。存在读仓调用时，`knowledge_before_repo` 表示首个检索是否早于首个读仓，只有读仓时为 `false`。没有读仓调用时该值为 `null`。可按 `seq` 核对各工具事件，模型随工具调用提供的初判可在相应 `llm_step` 查看。

## 离线复核

在仓库根目录、已激活项目虚拟环境的 Bash 中运行。

```bash
export PYTHONDONTWRITEBYTECODE=1
export FUNCGUARD_AGENT_RUNS_DIR="$(mktemp -d)"
export FUNCGUARD_LLM_PROVIDER=stub
python -m pytest -q -p no:cacheprovider
python -c 'import backend.server'
```

步数修复分支为 91 passed，流程分支为 103 passed，均有 1 项真实数据测试跳过。后端模块导入成功。顺序复核使用 2 条本地构造样本（1 对，`is_stub=true`），覆盖先检索、直接读仓、逆序与无工具调用。

## 真实模型复评

新提示词尚未经真实模型评测。仓库中 11 月 24 日的 300 条（150 对）预测来自 `legacy` 提示词，模型为 GLM-5.3-Flash（`is_stub=false`）。合入后需用真实模型与已审计的知识索引重跑，核对检索和读仓顺序，并与原记录比较 P-C、P-V、P-B、P-R。[原评测记录](2026-11-24-p7-full-eval.md)
