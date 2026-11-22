# FuncGurad

面向 C/C++ 的函数级漏洞检测智能体。

输入一个函数（含 commit 上下文），Agent 自主决定调用只读仓库工具（查找文件、搜索内容、读取片段），给出漏洞判定与理由，并输出可在快照中复核的证据路径。

## 当前进度

| 模块 | 状态 | 说明 |
|---|---|---|
| 仓库骨架与 JSON 契约 | 已完成 | 六个契约（Sample / ResultRecord / EvalReport / RepoSnapshot / KnowledgeHit / TraceEvent）与评测基线 |
| 仓库快照与函数定位 | 已完成 | 原生 git 克隆、父提交解析、快照导出、文本定位 v1；已用 6 条样本验证 |
| Agent 闭环 | 已完成 | 自主循环 + 三个只读工具 + 证据门控；`result.jsonl` 严格只含 `idx` 与 `prediction` |
| 图形界面与后端接口 | 已完成（stub） | Vue3 单页 + FastAPI；单条/批量/报告/轨迹接口，产物落 `cache/runs/` |
| 知识库 / RAG | 未实现 | `knowledge/`、`rag/` 目前只有占位 |
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

前端已构建并入库（`web/dist`），无需 `npm install` 即可使用。需要修改前端时再执行：

```powershell
cd web
npm install
npm run dev
```

### 本机代理

仓库不内置代理默认值。如果所在网络访问 GitHub 或模型接口需要代理，请自行设置：

```powershell
$env:FUNCGUARD_GIT_PROXY = "http://127.0.0.1:7897"
```

该变量同时用于 git 快照链路与模型请求；不设置时不走代理。模型请求也可单独用 `FUNCGUARD_LLM_PROXY` 覆盖。

## 目录说明

| 目录 / 文件 | 用途 |
|---|---|
| `bench/` | 测试集加载、`result.jsonl` 生成、P-C/P-V/P-B/P-R 评分 |
| `repo_service/` | commit URL 解析、原生 git 快照、函数定位、批量准备 |
| `repo_tools/` | Agent 只读工具：`find_files` / `grep_content` / `read_file` |
| `backend/` | Agent 循环、provider、运行编排、CLI |
| `backend/api/` | FastAPI 接口与后台任务编排 |
| `backend/server.py` | 一条命令启动 HTTP 服务 |
| `web/` | Vue3 单页（源码 + 免构建的 `dist`） |
| `shared/contracts/` | 六个 JSON Schema |
| `knowledge/`、`rag/` | 知识库与检索（尚未实现） |
| `docs/handoff/` | 各里程碑交接文档 |
| `cache/` | 本地缓存与运行产物，不入库 |

## 交接文档

各里程碑的交接文档与阅读顺序见 [docs/handoff/README.md](docs/handoff/README.md)。

## 约定

给 agent 看的文件统一放到 `.agents/` 目录里。
