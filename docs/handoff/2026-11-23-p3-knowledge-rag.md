# 交接：P3 知识库与 RAG（feat/p3-rag）

- 分支：`feat/p3-rag`
- 基线：`main` @ `38a3fc9`
- 日期：2026-11-23
- 范围：泄漏审计、知识卡片化、BM25 索引、检索接口与 Agent 工具、效果自测、界面入口

## 1. 范围与结论

已完成从本地 `knowledge.jsonl` 到可检索知识库的整条链路，并接进 FastAPI 与 Agent 工具，没有改契约字段、Agent 循环和 P1/P2 脚本：

- 泄漏审计：idx / commit / 规范化哈希均为 0 交集；函数内容层面发现 **25 对包含度 ≥0.8** 的近似记录，涉及 14 条知识记录，已默认从索引剔除。
- 清洗卡片：7,578 行 → 7,574 条唯一卡片 → 剔除 14 条 → **7,560 条入索引**；坏行 0，重复 idx 4 条保留首条。
- 检索：纯 Python BM25，中英文与驼峰/下划线分词；单次检索中位数 23 ms，P95 53 ms。
- 接口：`GET /api/knowledge/search` 返回严格符合 `KnowledgeHit` 的命中；界面新增“4. 知识库检索”区域。
- Agent：新增 `search_knowledge` 工具，工具调用写入 `trace.jsonl` 的 `repo_tool` 事件。
- 效果自测：40 个已知案例，CVE 描述 query 的 top-3 命中 40/40（**自检索上界，不代表泛化能力**），同项目不同 CVE 负例误命中 1/39。

一句话：BM25 单路已经可用，索引与接口都能复现；主要短板是没有向量检索、汇报里的 100% 是自检索数字、以及 0.5~0.8 的近似记录仍未处置。

## 2. 数据与产物

### 2.1 数据源（本地，不入库）

| 用途 | 路径 | 规模 |
|---|---|---|
| 知识库 | 课程材料中的 `knowledge.jsonl` | 52.5 MB / 7,578 行 / 3,266 个 CVE |
| 测试集 | `cache/dataset/test.jsonl` | 300 行 / 150 对 |

知识文件不在仓库内，通过 `--knowledge` 或环境变量 `FUNCGUARD_KNOWLEDGE_PATH` 传入。索引与产物全部写 `cache/knowledge/`，已被 `.gitignore` 的 `cache/` 规则忽略。

### 2.2 清洗与卡片

卡片字段（`cache/knowledge/cards.jsonl`，一行一条）：

| 字段 | 说明 |
|---|---|
| `ref` / `source_idx` | `knowledge:<idx>` 与原始 idx |
| `project` | 项目名，缺失记为 `unknown` |
| `cve` / `cwe` | CVE 编号（大写）、CWE 列表 |
| `commit_id` / `commit_url` | 修复提交 |
| `commit_message` | 截断 600 字符 |
| `func_signature` | 从函数体提取的签名，截断 200 字符 |
| `func_excerpt` | 函数体，截断 4,000 字符 |
| `cve_desc` | NVD 描述，截断 1,200 字符 |
| `file_name` / `target` / `project_url` | 原样保留，缺失为 `null` |

兜底规则：空字符串、`"None"`、`"null"` 统一视为空；`project` 缺失记 `unknown`；`cwe` 缺失为 `[]`；单条函数超长只截断不丢弃；JSON 坏行只计数不中断（本次 0 行）。

### 2.3 缓存占用（实测）

| 文件 | 大小 |
|---|---|
| `cards.jsonl` | 25.6 MiB（26,840,741 B） |
| `bm25.json` | 11.7 MiB（12,242,172 B） |
| `audit/leakage-audit.json` + `.md` | 约 10.8 KB |
| `selftest/selftest.json` + `.md` | 约 2.3 KB |
| 合计 | **37.3 MB** |

## 3. 索引与检索

### 3.1 为什么是 BM25

本机没有可离线使用的 embedding 模型（`sentence-transformers` 未安装），按约定不联网下载权重，因此只做 BM25 单路。`manifest.json` 的 `vector` 字段会记录未启用原因；如后续配置 `FUNCGUARD_EMBEDDING_MODEL` 且安装依赖，可在此基础上加向量召回。

### 3.2 分词与打分

- 英文单词与代码标识符：先按非字母数字切分，再拆驼峰（`readResidualRice`）与下划线（`read_residual_rice`），全部小写。
- 中文：连续汉字生成二元组，单字保留单字；语料主要是英文，中文 query 主要靠代码标识符命中。
- 停用词：过滤 `the / and / with / allow` 等常见英文词。
- BM25：`k1=1.5`、`b=0.75`；本次索引 108,287 个词项、1,167,558 条倒排、平均每条 467 个词。

### 3.3 重建索引（含泄漏剔除）

```powershell
$env:FUNCGUARD_KNOWLEDGE_PATH = "<knowledge.jsonl 路径>"

# 1) 审计：必须先跑，build 会读取审计结果剔除高相似条目
python -m knowledge.audit_leakage --knowledge $env:FUNCGUARD_KNOWLEDGE_PATH

# 2) 清洗 + 分词 + BM25，产物写 cache/knowledge/
python -m rag.build_index --knowledge $env:FUNCGUARD_KNOWLEDGE_PATH

# 3) 可选：检索自测，报告写 cache/knowledge/selftest/
python -m rag.selftest --samples 40 --top-k 3
```

- 不跑审计直接 build 会打印警告并保留全部条目；`--include-leaky` 可显式关闭剔除。
- 索引是进程内单例，重建后需重启 `python -m backend.server`。
- 换输出目录用 `--out` 或 `FUNCGUARD_KNOWLEDGE_DIR`。

### 3.4 实测耗时

| 阶段 | 耗时 |
|---|---|
| 泄漏审计 | 首次 30.77 s，复跑 10.16 s |
| 清洗卡片 | 0.71 s |
| 分词 | 8.64 s |
| BM25 建索引并落盘 | 2.84 s |
| **build 总计** | **12.19 s** |
| 单次检索（热） | 中位数 23.15 ms / P95 53.3 ms / 最大 80.44 ms |
| 首次工具调用（含加载 37 MB 缓存） | 约 1.24 s |

环境：索引构建用 Python 3.11.2，服务与接口验证用 Python 3.9.13；索引是纯 JSON，两个版本互通。

## 4. 接口与 Agent 接入

### 4.1 HTTP 接口

`GET /api/knowledge/search`

| 参数 | 必填 | 说明 |
|---|---|---|
| `q` | 是 | 检索词，支持中英文与代码标识符 |
| `top_k` | 否 | 1~20，默认 5 |
| `project` | 否 | 项目名，大小写不敏感 |
| `cwe` | 否 | 如 `CWE-787`，大小写不敏感 |

索引未构建时返回 503 和可读提示（`error.code = knowledge_not_ready`），不会 500。成功响应示例（真实返回，已截断）：

```json
{
  "ok": true,
  "data": {
    "query": "heap buffer overflow in image decoder",
    "retriever": "bm25",
    "top_k": 2,
    "filters": {"project": null, "cwe": null},
    "candidates": 7560,
    "hits": [
      {
        "ref": "knowledge:404869",
        "score": 15.7594,
        "source_idx": 404869,
        "project": "flac",
        "cve": "CVE-2014-9028",
        "cwe": ["CWE-119"],
        "func_signature": "FLAC__bool read_residual_partitioned_rice_(FLAC__StreamDecoder *decoder, ...",
        "summary": "CWE-119 Heap-based buffer overflow in stream_decoder.c in libFLAC before 1.3.1 ...",
        "retriever": "bm25"
      }
    ],
    "elapsed_ms": 18.66
  },
  "error": null
}
```

每个 `hit` 只有契约里的 9 个字段，未新增字段；`summary` 由 CWE + NVD 描述组成，不回填测试集代码或修复 diff。

### 4.2 Agent 工具

- 实现：`repo_tools/search_knowledge.py`；注册：`repo_tools/registry.py`（`_HANDLERS` + `TOOL_SCHEMAS` + `summarize`）。
- 参数：`query`（必填）、`top_k`（1~10）、`project`、`cwe`。
- 不依赖仓库快照；索引缺失时抛 `ToolError`，由 `dispatch` 转成 `ok=false`，不中断循环。
- Trace：沿用循环现有的 `repo_tool` 事件（`name = search_knowledge`），示例：

```json
{
  "type": "repo_tool",
  "status": "ok",
  "name": "search_knowledge",
  "duration_ms": 1235.28,
  "payload": {
    "step": 1,
    "arguments": {"query": "heap buffer overflow image decoder", "top_k": 3},
    "summary": {
      "ok": true,
      "total_matches": 3,
      "hits": [{"ref": "knowledge:404869", "cve": "CVE-2014-9028", "score": 15.7594}],
      "elapsed_ms": 6.27
    }
  }
}
```

TraceEvent 契约里另有 `knowledge_search` 类型，但按约束没有改 Agent 循环，因此统一走 `repo_tool`；若后续要区分，可在循环里按工具名映射事件类型。

### 4.3 界面

`web/src/App.vue` 新增“4. 知识库检索”面板：查询框、项目/CWE 过滤、top_k、命中列表（CVE、项目、分数、签名、摘要、CWE）。`web/dist` 已重新构建并入库，原有三个面板未改。

## 5. 验证结果

### 5.1 泄漏审计

- idx / commit / 规范化哈希：0 交集。
- CVE：测试集无 `cve` 字段，无法直接比对；测试函数文本出现 2 个 CVE，其中 CVE-2017-5226 在知识库中存在，需人工确认。
- 近似片段：25 对 ≥0.8（最高 0.9899），15 对 0.5~0.8。25 条测试样本受影响，占 150 对的 16.7%。
- 处置：14 条知识记录默认剔除，明细见 `docs/p3-knowledge-leakage-audit.md`。

### 5.2 检索自测（40 个案例，top_k=3）

| 指标 | 结果 |
|---|---|
| CVE 描述 query：top-3 命中同一 CVE | 40/40 = 100%（自检索上界） |
| CVE 描述 query：top-3 命中同一记录 | 39/40 = 97.5% |
| 同项目不同 CVE 负例：误命中目标 CVE | 1/39 = 2.6% |
| 修复提交标题 query：top-3 命中同一 CVE | 33/34 = 97.1% |
| 函数签名 query：top-3 命中同一 CVE | 38/40 = 95.0% |

失败样例：

- 负例：目标 CVE-2014-3597，用同项目 CVE-2014-4049 的描述检索，top-3 仍召回 CVE-2014-3597（第 3 位）。
- 函数签名：`void Compute(OpKernelContext* c) override` 这类通用签名在 tensorflow 中大量复用，query CVE-2021-37644 的前 3 条都是其它 CVE。
- 修复标题：CVE-2019-13137 的提交标题只是一个 issue 链接，缺少语义，top-3 全部跑偏。

### 5.3 端到端

- `GET /api/health`：200，provider 为 stub。
- `GET /api/knowledge/search?q=heap buffer overflow in image decoder&top_k=2`：200，返回 2 条，`elapsed_ms=18.66`。
- 过滤：`project=openssl&cwe=CWE-310` 时先按元数据缩小候选，再打分。
- 单条运行 `idx=194989`（配对 218785）：仍是 4 步 stub 流程，`P-B`，两版 `prediction=0`；报告里 `provider.tools` 已包含 `search_knowledge`。
- 用假 provider 强制调用一次 `search_knowledge`，`trace.jsonl` 写出 `repo_tool` 事件，工具返回 3 条命中。
- 首页 `GET /`：200；dist 中可检索到“知识库检索”面板。

## 6. 已知短板与下一步

### 6.1 已知短板

1. **只有 BM25**：同义词、改写表达召回弱；中文语料稀缺，中文 query 主要靠代码标识符命中。
2. **自测 100% 是自检索**：query 直接来自卡片自身的 CVE 描述，属于上界；真正有信息量的是负例 2.6% 和签名/标题探针。
3. **内容级泄漏仍有残余**：0.5~0.8 的 15 对未剔除；近似比较只取前 4,000 字符，超长函数尾部差异可能漏检。
4. **CVE 无法直接比对**：测试集没有 `cve` 字段，只能扫函数文本，覆盖不完整。
5. **冷启动 1.24 s**：每次进程首次检索要加载 37 MB 缓存；索引更新后必须重启服务。
6. **摘要可能提示修复方向**：`summary` 含 NVD 描述与提交标题，虽已剔除高相似条目，中相似条目仍可能给出方向性提示。
7. **界面很轻**：没有分页、项目下拉和结果详情页，只适合冒烟查看。

### 6.2 下一步可认领任务

- 接入离线 embedding（配置好本地模型后）做 BM25 + 向量混合召回，并在 `KnowledgeHit.retriever` 里区分 `bm25 / vector / hybrid`。
- 人工抽查 15 对 0.5~0.8 记录，决定是否扩大剔除阈值；把审计阈值做成参数。
- 给审计补“按函数名 + 项目”的统计，并评估 CVE 文本提及的覆盖缺口。
- 让 `/api/health` 汇报知识库卡片数与索引构建时间（需先与前端约定字段，避免破坏现有结构）。
- 批量评测时记录每次 `search_knowledge` 的命中与最终判定，统计“检索是否真的帮到模型”。
- 前端补分页与项目下拉；若新增交互，记得重新构建 `web/dist`。
