# FuncGurad

面向 C/C++ 的函数级漏洞检测智能体。

输入一个函数（含 commit 上下文），Agent 自主决定调用只读仓库工具（查找文件、搜索内容、读取片段），给出漏洞判定与理由，并输出可在快照中复核的证据路径。

## 当前进度

| 模块 | 状态 | 说明 |
|---|---|---|
| 仓库骨架与 JSON 契约 | 已完成 | 六个契约（Sample / ResultRecord / EvalReport / RepoSnapshot / KnowledgeHit / TraceEvent）与评测基线 |
| 仓库快照与函数定位 | 已完成 | 原生 git 克隆、父提交解析、快照导出、文本定位 v1；已用 6 条样本验证 |
| Agent 闭环 | 已完成 | 自主循环 + 四个只读工具（含知识检索）+ 证据门控；`result.jsonl` 严格只含 `idx` 与 `prediction` |
| 图形界面与后端接口 | 已完成（stub） | Vue3 单页 + FastAPI；单条/批量/报告/轨迹/知识库检索接口，产物落 `cache/runs/` |
| 知识库 / RAG | 已完成（BM25） | 泄漏审计 + 知识卡片 + 纯 Python BM25；7,560 条入索引，单次检索约 20 ms；无向量检索 |
| 真实模型接入 | 未接入 | 当前没有模型 key，自动回退到确定性 stub provider |
| 全量评测 | 未执行 | 只验证了 1 对样本（idx 194989 / 218785），尚未跑 300 条全量 |

> 当前所有检测结论均由 stub provider 生成，只用于验证链路，**不代表真实检测能力**。

## 快速开始

需要 Python 3.9+。

```powershell
python -m pip install -r requirements.txt
python -m backend.server --open
```

启动后浏览器会打开 `http://127.0.0.1:8000`，界面顶部会标注“占位模型”。

> 没有本地数据时首页仍可打开，但运行会提示缺少数据（HTTP 503），不会静默失败。

前端已构建并入库（`web/dist`），无需 `npm install` 即可使用。需要修改前端时再执行：

```powershell
cd web
npm install
npm run dev
```

## 运行前准备

以下数据是本地课程数据，**不入库**，需要自行生成或从课程材料获取：

| 路径 | 内容 |
|---|---|
| `cache/dataset/test.jsonl` | 300 条测试样本（150 对） |
| `cache/meta/samples/*.json` | 每个 idx 的快照元数据（含 `snapshot_dir`） |
| `cache/snapshots/*` | 只读仓库快照，供 Agent 工具检索 |
| `knowledge.jsonl`（仓库外） | 历史 CVE 知识库，用于构建检索索引 |

准备方式：

1. 从课程材料获取 `test.jsonl`，放到 `cache/dataset/test.jsonl`。
2. 按 [P2 交接文档](docs/handoff/2026-11-22-p2-snapshot.md) 的流程生成快照与元数据，例如：
   `python -m repo_service.prepare --only-idx 194989 --only-idx 218785`
3. 运行数据自检：

```powershell
python -m backend.cli check
```

自检会逐项列出测试集、样本元数据、快照目录和 provider 的状态，缺失时给出准备提示；全部就绪时退出码为 0。

### 本机代理

仓库不内置代理默认值。如果所在网络访问 GitHub 或模型接口需要代理，请自行设置：

```powershell
$env:FUNCGUARD_GIT_PROXY = "http://127.0.0.1:7897"
```

该变量同时用于 git 快照链路与模型请求；不设置时不走代理。模型请求也可单独用 `FUNCGUARD_LLM_PROXY` 覆盖。

## 知识库检索

知识库文件是本地课程数据，不入库。构建索引（产物写 `cache/knowledge/`）：

```powershell
$env:FUNCGUARD_KNOWLEDGE_PATH = "<knowledge.jsonl 路径>"
python -m knowledge.audit_leakage --knowledge $env:FUNCGUARD_KNOWLEDGE_PATH
python -m rag.build_index --knowledge $env:FUNCGUARD_KNOWLEDGE_PATH
```

- 审计必须先跑：build 会剔除与测试集函数包含度 ≥0.8 的知识记录，避免检索泄漏答案。
- 单次构建约 12 s，缓存约 37 MB；重建索引后需重启服务。
- 使用入口：界面“4. 知识库检索”面板；`GET /api/knowledge/search?q=...&top_k=5&project=...&cwe=...`；Agent 工具 `search_knowledge`。
- 未构建索引时检索接口返回 503，不影响单条样本运行。

详见 [P3 交接文档](docs/handoff/2026-11-23-p3-knowledge-rag.md) 与 [泄漏审计报告](docs/p3-knowledge-leakage-audit.md)。

## 目录说明

| 目录 / 文件 | 用途 |
|---|---|
| `bench/` | 测试集加载、`result.jsonl` 生成、P-C/P-V/P-B/P-R 评分 |
| `repo_service/` | commit URL 解析、原生 git 快照、函数定位、批量准备 |
| `repo_tools/` | Agent 只读工具：`find_files` / `grep_content` / `read_file` / `search_knowledge` |
| `backend/` | Agent 循环、provider、运行编排、CLI |
| `backend/api/` | FastAPI 接口与后台任务编排 |
| `backend/server.py` | 一条命令启动 HTTP 服务 |
| `web/` | Vue3 单页（源码 + 免构建的 `dist`） |
| `shared/contracts/` | 六个 JSON Schema |
| `knowledge/` | 知识库脚本：泄漏审计 |
| `rag/` | 知识卡片、BM25 索引、检索接口与自测 |
| `docs/handoff/` | 各里程碑交接文档 |
| `cache/` | 本地缓存与运行产物，不入库 |

## 交接文档

各里程碑的交接文档与阅读顺序见 [docs/handoff/README.md](docs/handoff/README.md)。

## 约定

给 agent 看的文件统一放到 `.agents/` 目录里。
