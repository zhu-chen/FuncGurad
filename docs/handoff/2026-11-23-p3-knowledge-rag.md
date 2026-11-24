# 交接：P3 知识库与 RAG（feat/p3-rag，含 0.5 严格口径复核）

- 分支：`feat/p3-rag`（已由 PR #7 以 merge commit 合入 `main`）
- 基线：`main` @ `38a3fc9`
- 日期：2026-11-23（严格口径复核同日完成）
- 范围：泄漏审计、知识卡片、纯 Python BM25 索引、检索接口、Agent 工具、前端检索面板

## 0. 一分钟接手

### 0.1 本地数据

| 数据 | 来源 | 路径 | 自检 | 缺失时的现象 |
|---|---|---|---|---|
| 测试集 300 条 / 150 对 | 课程材料 | `cache/dataset/test.jsonl` | `python -m backend.cli check` | `/api/health` 返回 `status=degraded`（HTTP 200、`ok=false`），单条/批量运行返回 503；首页顶部出现黄色的数据缺失横幅 |
| 知识库 7,578 行 | 课程材料 | 仓库外，任意路径；用 `FUNCGUARD_KNOWLEDGE_PATH` 指向 | `python -m knowledge.audit_leakage --knowledge <路径>` | 无法建索引；已有索引时检索仍可用；没有索引时 `/api/knowledge/search` 返回 503 `knowledge_not_ready` |
| 快照与元数据 | `python -m repo_service.prepare --only-idx <idx>` | `cache/meta/samples/*.json`、`cache/snapshots/*` | `python -m backend.cli check` | 单条运行在工具阶段报“找不到快照/文件” |
| 知识索引 | `python -m knowledge.audit_leakage` + `python -m rag.build_index` | `cache/knowledge/` | 见 0.2 | 前端“4. 知识库检索”面板显示 503 提示；不影响单条样本运行 |
| 前端构建产物 | 已入库 | `web/dist/` | 打开首页 | 缺失时首页 503，提示先 `npm run build` |

`cache/` 全部不入库。换机器后必须重新审计与建索引，不能假设旧索引还在。

### 0.2 从零跑通（PowerShell）

```powershell
python -m pip install -r requirements.txt
python -m backend.cli check

$env:FUNCGUARD_KNOWLEDGE_PATH = "<knowledge.jsonl 路径>"
python -m knowledge.audit_leakage --knowledge $env:FUNCGUARD_KNOWLEDGE_PATH --threshold 0.5
python -m rag.build_index --knowledge $env:FUNCGUARD_KNOWLEDGE_PATH
python -m knowledge.audit_leakage --cards --threshold 0.5   # 残余应为 0 对

python -m backend.server --open
```

### 0.3 环境坑（本机实测）

1. **Python 版本**：要求 3.9+。本机默认 `python` 是 3.11.2，但没有 FastAPI/uvicorn；
   带依赖的是 `D:\anaconda\python.exe`（3.9.13）。**审计/建索引/自测**用哪个解释器都行，
   **起服务**要用装好依赖的那个。
2. **PYTHONPATH 污染**：如果全局 `PYTHONPATH` 指向别的项目，venv 里会出现“包装了却
   import 到别处”的假成功。先 `python -c "import sys; print(sys.path)"` 确认；必要时用
   干净 venv，并显式用该 venv 的 `python -m pip install -r requirements.txt`。
3. **代理**：访问 GitHub 或模型接口需要显式设置 `FUNCGUARD_GIT_PROXY=http://127.0.0.1:7897`；
   不设置就不走代理。模型请求可单独用 `FUNCGUARD_LLM_PROXY` 覆盖。
4. **索引更新后必须重启服务**：检索器在进程启动时加载 `cache/knowledge/`；重建索引后不重启
   仍会返回旧结果或 503。
5. **PowerShell 5**：不支持 heredoc、`&&`、`||`；多行 Python 写成临时 `.py` 文件再执行，
   或用分号串联。变量名 `$home` 是只读的，脚本里换名。
6. **控制台编码**：中文输出乱码时设置 `$env:PYTHONIOENCODING="utf-8"` 或 `chcp 65001`。
7. **4,000 字符截断**：近似检测只比较函数前 4,000 个规范化字符；超长函数尾部不参与。

### 0.4 当前真实完成度（先看这里）

| 能力 | 状态 | 说明 |
|---|---|---|
| 泄漏审计（idx / commit / 哈希 / 8-gram / CVE 文本） | **真实实现** | 阈值 0.5；剔除 77 条；最终卡片残余 0 对，最高包含度 0.4941 |
| 知识卡片 | **真实实现** | 7,574 条唯一卡片（原始 7,578 行，重复 4，坏行 0），剔除后 **7,497** 条入索引 |
| BM25 检索 | **真实实现** | 纯 Python，k1=1.5、b=0.75；107,767 词项、1,154,782 条倒排、11.6 MB |
| `GET /api/knowledge/search` | **真实可用** | 实测 `candidates=7497`，单次 17.55 ms |
| Agent 工具 `search_knowledge` | **已接入** | 注册在 `repo_tools/registry.py`，写 `repo_tool` 轨迹；未改 Agent 循环 |
| 前端知识库检索面板 | **真实可用** | `web/dist` 已重建并入 main |
| 向量检索 | **未实现** | 未安装 `sentence-transformers`，`manifest.retriever=bm25` 单路 |
| 真实模型 | **未接入** | 无 key，自动回退确定性 stub；所有检测结论只验证链路 |
| 300 条全量评测 | **未执行** | 只验证过 1 对样本（194989 / 218785） |
| 泛化能力 | **未验证** | 自测正例 100% 是自检索上界（query 来自卡片自身 NVD 描述），不能当泛化指标 |

## 1. 范围与结论

已完成“知识库不进测试集 + 可检索 + 可被 Agent 调用”的闭环：

- 知识库 7,578 行 → 7,574 条唯一卡片 → 严格剔除 77 条 → **7,497 条入索引**。
- 检索接口与 Agent 工具都按 `KnowledgeHit` 契约返回，字段不增不减。
- 前端新增“4. 知识库检索”面板，只做展示与交互。
- 实测：`/api/health` 200；`/api/knowledge/search?q=CVE-2014-9028...` 返回 3 条，
  首条 `knowledge:404869`（flac / CVE-2014-9028），17.55 ms；首页 200。

## 2. 数据与产物

### 2.1 清洗与卡片化

- 以 `idx` 去重，保留首条；坏行跳过。
- 卡片字段：`source_idx`、`ref`、`project`、`cve`、`cwe`、`func_signature`、
  `commit_message`、`cve_desc`、`func_excerpt`（原始函数前 4,000 字符）。
- 汇总：7,578 行 → 7,574 张卡片；重复 4；坏行 0；最长函数截断到 4,000 字符。

### 2.2 索引产物

| 文件 | 大小 | 说明 |
|---|---|---|
| `cache/knowledge/cards.jsonl` | 25.3 MiB | 7,497 条卡片（已剔除） |
| `cache/knowledge/bm25.json` | 11.6 MB | 倒排、文档长度、词项统计 |
| `cache/knowledge/manifest.json` | 2.1 KB | 源规模、剔除阈值与条数、构建耗时 |
| `cache/knowledge/audit/` | ~0.15 MB | 知识库审计 + 最终卡片残余审计 |
| `cache/knowledge/selftest/` | <0.01 MB | 自测报告与固定案例 |

建索引耗时（7,497 条）：卡片 0.78 s + 分词 11.72 s + 索引 3.58 s = **16.08 s**；
缓存合计约 37 MB。

## 3. 检索与索引

- 分词：英文按词、中文按双字、驼峰与下划线切分，统一小写；不依赖第三方库。
- 打分：BM25（k1=1.5，b=0.75），返回原始分，不做归一化。
- 过滤：`project` 精确匹配（忽略大小写），`cwe` 按 `CWE-xxx` 包含匹配。
- 返回：`candidates`（参与打分的文档数）、`hits`（最多 `top_k` 条）、`elapsed_ms`。
- 每条命中严格符合 `shared/contracts/knowledge_hit.schema.json`：
  `ref` / `score` / `source_idx` / `project` / `cve` / `cwe` / `func_signature` /
  `summary` / `retriever`，`additionalProperties=false`。
- 索引缺失时抛 `KnowledgeIndexError`，接口返回 503 `knowledge_not_ready`。

## 4. 接口与 Agent 接入

### 4.1 HTTP

| 方法 | 路径 | 参数 | 说明 |
|---|---|---|---|
| GET | `/api/knowledge/search` | `q`（必填）、`top_k` 1~20、`project`、`cwe` | 知识检索 |

调用示例：

```powershell
Invoke-RestMethod "http://127.0.0.1:8000/api/knowledge/search?q=CVE-2014-9028%20heap%20buffer%20overflow&top_k=3"
```

响应示例（实测）：

```json
{
  "ok": true,
  "data": {
    "candidates": 7497,
    "elapsed_ms": 17.55,
    "hits": [
      {"ref": "knowledge:404869", "score": 32.3354, "source_idx": 404869,
       "project": "flac", "cve": "CVE-2014-9028", "cwe": ["CWE-787"],
       "func_signature": "...", "summary": "...", "retriever": "bm25"}
    ]
  },
  "error": null
}
```

### 4.2 Agent 工具

- 名称：`search_knowledge`，参数 `query`、`top_k`、`project`、`cwe`。
- 注册：`repo_tools/registry.py`；轨迹事件沿用 `repo_tool`，**未改 Agent 循环**。
- stub provider 下工具调用顺序是确定性的，只证明链路通，不代表真实决策质量。

## 5. 验证结果

### 5.1 泄漏审计（严格 0.5）

| 维度 | 结果 |
|---|---|
| idx 交集 | 0 |
| commit 交集 | 0 |
| 规范化哈希交集 | 0 |
| 近似配对（全部 ≥0.5） | 178 对（≥0.8 115，0.5~0.8 63） |
| 近似重复剔除 | 75 条 |
| CVE 文本提及剔除 | 2 条（CVE-2017-5226，idx 3020 / 160379） |
| 合计剔除 | **77 条** |
| 最终入索引 | **7,497 条** |
| 卡片残余审计 | **0 对**，最高 0.4941 |

为什么从 0.8 收紧到 0.5、原 0.5~0.8 的 15 对逐条判断、残余风险，见
[泄漏审计报告](../p3-knowledge-leakage-audit.md)。

### 5.2 检索自测（同一批 40 个固定案例）

| 指标 | 收紧前（7,560 条，0.8） | 收紧后（7,497 条，0.5） |
|---|---|---|
| CVE 描述 query：top-3 命中同一 CVE | 40/40 = 100% | 40/40 = 100% |
| CVE 描述 query：top-3 命中同一记录 | 39/40 = 97.5% | 39/40 = 97.5% |
| 同项目不同 CVE 负例：误命中 | 1/39 = 2.6% | 1/39 = 2.6% |
| 修复提交标题 query：top-3 命中同一 CVE | 33/34 = 97.1% | 33/34 = 97.1% |
| 函数签名 query：top-3 命中同一 CVE | 38/40 = 95.0% | 38/40 = 95.0% |
| 单次检索中位数 / P95 / 最大 | 23.15 / 53.30 / 80.44 ms | 19.69 / 40.92 / 53.20 ms |

40 个案例没有一个因剔除而消失（`cases_missing_from_index=[]`），因此没有“查询命中不到”
的情况；上述数字未做任何调参。正例 100% 仍是自检索上界，不能当泛化能力。

### 5.3 服务实跑（本机 8010 端口）

```
/api/health            ok=True  provider=stub  dataset=300/150
/api/knowledge/search  ok=True  candidates=7497  elapsed_ms=17.55
  HIT knowledge:404869 flac CVE-2014-9028 score=32.3354
  HIT knowledge:208089 flac CVE-2014-9028 score=32.0828
  HIT knowledge:6239   Chrome CVE-2014-3157 score=17.2459
/                      status=200  bytes=427
```

> `/api/health` 在缺数据时返回 HTTP 200 但 `ok=false`、`status=degraded`，前端据此显示横幅；
> 运行接口才返回 503。不要把“health 200”误读为“数据齐全”。

## 6. 已知短板

1. 只有 BM25，没有向量检索；同义改写、跨语言描述召回弱。
2. 自测正例来自卡片自身 NVD 描述，是自检索上界，不代表真实查询泛化。
3. 近似检测是 8-gram 词面包含度，且只比较前 4,000 字符；改标识符/重排/尾部差异可能漏检。
4. 同项目同类型但低于 0.5 的记录仍可能通过 `cve_desc` / 提交标题提示漏洞方向；
   为避免掏空知识库，本次未做同项目整体剔除。
5. 测试集没有 `cve` 字段，CVE 维度只能靠函数文本提及，覆盖不完整。
6. 索引是本地缓存，不入库；换机器必须重跑审计与建索引。
7. 真实模型未接入，Agent 工具调用顺序仍是 stub 的确定性逻辑。

## 7. 剩余待办与认领方式

按“能否独立开工”排序；每条都给出目标、改动目录、依赖与验收标准。

| # | 任务 | 目标 | 改哪里 | 依赖 | 验收标准 |
|---|---|---|---|---|---|
| 1 | 接真实模型跑通一对 | 用 OpenAI 兼容模型跑 1 对样本（194989 / 218785），产出非 stub 报告 | `backend/agent/`、provider 配置 | 模型 key | `is_stub=false`；`trace.jsonl` 有真实 `llm_step`；报告可复现；失败时给出可读错误（2026-11-24 已完成并合并） |
| 2 | 300 条全量评测 | 跑完 150 对，输出 P-C / P-V / P-B / P-R 与混淆矩阵 | `bench/`、`backend/api/` | 任务 1 的真实模型 | `EvalReport` 覆盖 300 条；给出整体指标、失败样本列表与运行日志 |
| 3 | 向量检索 / hybrid | 离线 embedding 接入，BM25 + 向量融合 | `rag/`（新增 embedder、hybrid retriever） | 模型权重与磁盘 | `manifest.retriever=hybrid`；自测不劣于 BM25；记录构建时间与体积；未装依赖时仍能回退 BM25 |
| 4 | 高相似样本处置复核 | 数据或阈值变化后重跑审计，确认残余仍为 0 | `knowledge/`、`rag/` | 新知识库/新测试集 | `audit` 残余 0 对；剔除清单可复现；报告数字同步 |
| 5 | README 承诺项验证 | 把 README 中的“未执行/未接入”逐项落实或改口径 | `README.md`、`docs/` | 任务 1、2、3 | README 数字与 `manifest.json`、自测报告一致；不再出现未验证承诺 |
| 6 | 索引产物一致性 | 换机器后按文档重建，确认 7,497 条与检索结果一致 | `cache/knowledge/`（本地） | 知识库与测试集 | `manifest.source.cards_indexed=7497`；`/api/knowledge/search` 正常；审计残余 0 |

> 任务 4 的“那 25 对高相似样本最终处置”已在本轮完成：25 对 ≥0.8 与 15 对 0.5~0.8
> 全部剔除，最终残余 0；若知识库或测试集更新，需要按同一脚本重跑。

## 8. 禁区

1. **不改 contracts 字段**：`shared/contracts/` 下六个 schema 的字段与语义不动；
   `KnowledgeHit` 的 `additionalProperties=false` 不能放开。
2. **不把测试集或知识库提交进仓库**：`knowledge.jsonl`、`test.jsonl` 都是课程数据。
3. **不提交缓存**：`cache/` 只存本地产物；`web/node_modules/` 同样不入库。
4. **不放宽泄漏阈值**：0.5 是当前底线，不得为了自测分数调回 0.8；
   `--include-leaky` 只用于研究，不得用于评测或演示。
5. **不在 Agent 循环写死工具调用顺序**：工具由模型决策；stub 的确定性顺序只用于测试。
6. **不在代码/文档写本机绝对路径或密钥**：路径用 `cache/...` 相对写法；凭证只从环境变量读。

## 9. 合并记录

- PR #7（`feat/p3-rag`）以 merge commit 合入 `main`，`main` @ `2329983`
  （`23299837ceee935e7b6d1661f9e856b92d0eb72a`）。
- 严格口径复核在 `fix/leakage-strict` 完成，合并后 `main` 的最终库规模为 7,497 条；
  合并 sha 见最终回复与 `git log`。
