# 交接：P3 泄漏隔离收紧到 0.5（fix/leakage-strict）

- 分支：`fix/leakage-strict`
- 基线：`main` @ `2329983`（PR #7 已合入 P3）
- 日期：2026-11-23
- 范围：把知识库隔离标准从“每测试样本最佳匹配 ≥0.8”收紧为“任一配对 ≥0.5”，
  重建卡片与 BM25，并复测检索

## 1. 为什么改

作业要求测试集不得进入知识库。旧口径只排除与测试样本最佳匹配 ≥0.8 的记录，
存在两个漏洞：

1. 0.5~0.8 的内容近似仍留在索引里（旧口径 15 对）；
2. 一条知识记录只要不是某个测试样本的“最佳匹配”，即使与另一个样本高度相似也不会被剔除。

严格口径改为：**枚举所有 ≥0.5 的 (测试样本, 知识记录) 配对，涉及的记录全部剔除**；
测试函数注释里出现的 CVE 若知识库有同编号记录，也一并剔除。

## 2. 改了什么

| 文件 | 变更 |
|---|---|
| `knowledge/audit_leakage.py` | 默认阈值 0.5；枚举全部 ≥阈值配对；每条函数按 `full` 与 `raw4000` 两种文本形态取最大包含度；新增 `--threshold` 与卡片残余审计 `--cards`；输出顶层 `excluded_knowledge_idxs` 与 `exclusion` 拆分 |
| `rag/build_index.py` | 从审计结果读取真实阈值与剔除清单；合并卡片残余审计的剔除项；`manifest.leakage_filter` 记录阈值、剔除数与审计文件 |
| `rag/selftest.py` | 新增 `--cases-file` / `--case-idxs`，支持用收紧前同一批 query 复测；报告 `cases_source` 与 `cases_missing_from_index` |
| `docs/p3-knowledge-leakage-audit.md` | 按 0.5 口径重写，含 15 对逐条判断与残余风险 |
| `docs/handoff/2026-11-23-p3-knowledge-rag.md` | 更新库规模、自测、接手须知、环境坑、完成度、待办、禁区 |
| `README.md`、`docs/handoff/README.md` | 同步阈值、库规模与索引 |

未改 `shared/contracts/`、Agent 循环、P1/P2 脚本与前端；`web/dist` 无需重建。

## 3. 结果

| 项目 | 收紧前（0.8） | 收紧后（0.5） |
|---|---|---|
| 配对方式 | 每测试样本最佳 | 全部 ≥0.5 配对 |
| 配对总数 | 40 | 178（≥0.8 115，0.5~0.8 63） |
| 近似重复剔除 | 14 条 | 75 条 |
| CVE 文本提及剔除 | 0 | 2 条（CVE-2017-5226，idx 3020 / 160379） |
| 合计剔除 | 14 条 | **77 条** |
| 入索引卡片 | 7,560 | **7,497** |
| 最终残余配对 | 15 对（0.5~0.8） | **0 对**，最高 0.4941 |

原始知识库 7,578 行 → 7,574 条唯一卡片（重复 4、坏行 0）→ 剔除 77 → 7,497。

## 4. 原 0.5~0.8 的 15 对

全部剔除。5 条涉及的知识记录是 `195307`、`197145`、`251920`、`197093`、`243095`，
均为 tensorflow。逐条判断（同一漏洞不同表述 / 同项目同类型相似代码 / 实质重复）
见 [泄漏审计报告第 5 节](../p3-knowledge-leakage-audit.md#5-原-0508-的-15-对逐条判断)：

- **同一漏洞的不同表述**：2 对（`271526↔251920`、`198282↔251920`），
  同属 CVE-2021-29611 修复，测试取 `ReshapeSparseTensor` 辅助函数，知识库取调用方。
- **同项目同类型的相似代码**：13 对，包括同一 Dequantize / FractionalMaxPoolGrad
  算子的不同提交版本，以及仅共享 `Compute` / `context->input` 模板的配对。
- **实质重复**：0 对；15 对都没有达到规范化哈希全等，按 0.5 内容近似口径全部按污染处理。

## 5. 检索自测对比

用收紧前同一批 40 个案例（`cache/knowledge/selftest/cases-before.json`）复测：

| 指标 | 收紧前 | 收紧后 |
|---|---|---|
| CVE 描述 top-3 同 CVE | 40/40 | 40/40 |
| CVE 描述 top-3 同记录 | 39/40 | 39/40 |
| 负例误命中 | 1/39 | 1/39 |
| 提交标题探针 | 33/34 | 33/34 |
| 签名探针 | 38/40 | 38/40 |
| 中位数 / P95 / 最大 | 23.15 / 53.30 / 80.44 ms | 19.69 / 40.92 / 53.20 ms |

40 个案例没有一个被剔除（`cases_missing_from_index=[]`），所以不存在“查询命中不到”；
没有为数字好看调参。正例 100% 是自检索上界，不能当泛化能力。

## 6. 复现

```powershell
$env:FUNCGUARD_KNOWLEDGE_PATH = "<knowledge.jsonl 路径>"
python -m knowledge.audit_leakage --knowledge $env:FUNCGUARD_KNOWLEDGE_PATH --threshold 0.5
python -m rag.build_index --knowledge $env:FUNCGUARD_KNOWLEDGE_PATH
python -m knowledge.audit_leakage --cards --threshold 0.5
python -m rag.selftest --cases-file cache\knowledge\selftest\cases-before.json --top-k 3
```

验收：`manifest.source.cards_indexed=7497`、`leakage_filter.threshold_containment=0.5`、
卡片审计残余 0 对；重启服务后 `/api/knowledge/search` 的 `candidates=7497`。

## 7. 残余风险

1. 8-gram 是词面近似；改标识符、重排、语义改写的同一漏洞可能低于 0.5 而漏检。
2. 只比较函数前 4,000 个规范化字符，超长函数尾部差异不计入。
3. 同项目同类型但低于 0.5 的记录仍可能通过 `cve_desc` / 提交标题提示方向；未整体剔除。
4. 测试集没有 `cve` 字段，CVE 维度只能靠函数文本提及，覆盖不完整。
5. 索引在 `cache/`，换机器必须重跑审计与建索引；不要复用旧产物。
6. `--include-leaky` 是研究开关，评测/演示禁用。

## 8. 合并记录

- 分支：`fix/leakage-strict`
- 合并方式：merge commit 合回 `main`
- 最终库规模：7,497 条；阈值 0.5；残余 0 对
- 合并后的 `main` sha 见最终回复与 `git log --oneline -1`
