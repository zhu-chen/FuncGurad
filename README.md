# FuncGurad

FuncGurad 是中国科学院大学（国科大）2026 年秋季《高级软件工程》课程大作业，选题为「函数漏洞检测智能体工具」。给定一个 C/C++ 函数和修复提交链接，课程要求 Agent 先检索历史 CVE 案例并初判可疑点，再根据证据需要读取对应版本仓库中的调用方、被调用方或声明，最后通过图形界面展示判定、理由和分析记录。见[课程要求速查](ref/3.%20函数漏洞检测智能体工具.md)和[课程要求与实现对照](docs/handoff/2026-11-24-course-requirements.md)。

## 当前进度

| 模块 | 状态 | 说明 |
|---|---|---|
| 仓库骨架与 JSON 契约 | 已完成 | 六个契约（Sample / ResultRecord / EvalReport / RepoSnapshot / KnowledgeHit / TraceEvent）与评测基线 |
| 仓库快照与函数定位 | 已完成 | 原生 git 克隆、父提交解析、快照导出、文本定位 v1；已用 6 条样本验证 |
| Agent 闭环 | 自主工具调用已接入 | 本地循环提供四个只读工具和证据门控。目前由模型选择是否检索知识库，提示词要求先定位函数，课程要求的检索顺序仍待落实 |
| 图形界面与后端接口 | 已完成 | Vue3 + FastAPI 支持按 idx、手工输入函数 + 提交链接、上传 `.jsonl` 三种入口；可实时查看轨迹与报告。手工/上传样本不读测试集，首次运行会现场准备快照 |
| 知识库 / RAG | 已完成（BM25） | 严格泄漏隔离（阈值 0.5）+ 知识卡片 + 纯 Python BM25；7,497 条入索引，单次检索约 20 ms；无向量检索 |
| 真实模型接入 | 已完成（需本地 key） | 已接入 GLM-5.3-Flash（智谱 OpenAI 兼容接口）；未配置 key 时自动回退 stub |
| 全量评测 | 已完成 | 300/300 样本、150/150 对；P-C 55 / P-V 67 / P-B 20 / P-R 8，等级 A；真实快照 194 条，合成兜底 106 条 |

> 仓库不包含 API key。配置 `.env.local` 后使用真实模型；未配置时结论由 stub provider 生成，仅用于验证链路，**不代表真实检测能力**。全量评测已完成：300 条预测全部参与评分，P-C 55 / P-V 67 / P-B 20 / P-R 8，最终等级 A。其中 194 条使用真实仓库快照，106 条因 GitHub 限流或 commit 不可达使用合成函数文本快照；仅看真实快照子集（94 对）为 P-C 36 / P-V 41 / P-B 13 / P-R 4，等级 A。详见 [P7 全量评测](docs/handoff/2026-11-24-p7-full-eval.md)。

## 快速开始

### Python 环境

需要 Python 3.9+。推荐使用 uv，也支持标准库 venv 和 pip。两种方式共用 `requirements.txt`（运行依赖）和 `requirements-dev.txt`（额外的测试依赖）。增减依赖时编辑对应文件即可。

在仓库根目录按下表配置环境。已有 `.venv` 时直接从激活步骤开始。

| 步骤 | uv（推荐） | venv + pip |
|---|---|---|
| 首次创建 | `uv venv --seed .venv`（含 pip） | `python -m venv .venv` |
| 激活（Windows PowerShell） | `.\.venv\Scripts\Activate.ps1` | `.\.venv\Scripts\Activate.ps1` |
| 激活（Linux / macOS 的 bash 或 zsh） | `source .venv/bin/activate` | `source .venv/bin/activate` |
| 安装运行依赖 | `uv pip install -r requirements.txt` | `python -m pip install -r requirements.txt` |
| 开发及测试时追加 | `uv pip install -r requirements-dev.txt` | `python -m pip install -r requirements-dev.txt` |

### 启动服务

在已激活的环境中执行。下面以 PowerShell 为例，Linux / macOS 用户可用 `cp` 复制配置模板。

```powershell
# 配置真实模型（一次性）：复制模板并填入自己的 API Key
Copy-Item .env.example .env.local
# 编辑 .env.local，至少设置 FUNCGUARD_LLM_API_KEY

python -m backend.cli check      # 应显示 provider: openai-compatible, is_stub=False
python -m backend.server --open
```

启动后浏览器会打开 `http://127.0.0.1:8000`。配置了真实模型时界面使用 GLM-5.3-Flash；未配置 key 时自动回退到确定性 stub，界面顶部会标注“占位模型”。

> `.env.local` 已被 `.gitignore` 忽略，不会进入 git。真实环境变量的优先级高于文件内容。
>
> 没有本地数据时首页仍可打开，但运行会提示缺少数据（HTTP 503），不会静默失败。

前端已构建并入库（`web/dist`），无需 `npm install` 即可使用。需要修改前端时再执行：

```powershell
cd web
npm install
npm run dev
```

### 图形界面输入方式

| 入口 | 输入 | 说明 |
|---|---|---|
| 按 idx | 测试集样本编号 | 自动补齐相邻配对样本，可计算 P-x |
| 手工样本 | 函数代码 + 修复提交链接 + 版本（修复前 / 修复后） | 不依赖测试集；版本由用户显式选择，模型只收到该版本快照 |
| 上传 `.jsonl` | 每行 `idx` / `target` / `commit_url` / `func` | 逐条独立推理，不要求按对出现，不算 P-x |

- 手工单样本的 `idx` 可留空，报告里记为 `0` 并标注为手工样本；`result.jsonl` 仍只含 `idx` 与 `prediction`。
- 手工与上传任务会先按提交链接准备仓库快照，大仓库可能需要数分钟，界面状态显示「准备快照」；产物写在 `cache/runs/<run_id>/`，不参与全量评测。
- 对应接口：`POST /api/runs/manual`、`POST /api/batches/upload`。详见 [P8 图形输入](docs/handoff/2026-11-25-p8-manual-input.md)。

## 真实模型配置

默认接入智谱开放平台的 OpenAI 兼容模型接口：

| 配置 | 默认值 |
|---|---|
| Base URL | `https://open.bigmodel.cn/api/paas/v4` |
| Model | `glm-5.3-flash` |
| Key 环境变量 | `FUNCGUARD_LLM_API_KEY`（也支持 `ZAI_API_KEY` / `ZHIPUAI_API_KEY` / `OPENAI_API_KEY`） |
| 最大输出 | 16384 tokens（`FUNCGUARD_LLM_MAX_TOKENS`） |
| 单样本总超时 | 600 s（`FUNCGUARD_AGENT_TIMEOUT`） |
| 单次模型请求超时 | 180 s（`FUNCGUARD_LLM_TIMEOUT`） |

- 启动时自动读取仓库根目录的 `.env.local` 与 `.env`，真实环境变量优先；这两个文件不入库。
- `FUNCGUARD_LLM_PROVIDER=auto`（默认）：有 key 时使用真实模型，无 key 时回退 stub；设为 `zhipu` / `openai` / `glm` 时缺少 key 会直接报错。
- provider 使用 OpenAI Chat Completions + function calling 协议，模型在本地 Agent 循环中调用四个只读工具，最终结论仍经过证据门控。
- **模型接入已完成**：配置 key 后 provider 会直接调用 `glm-5.3-flash`，支持 function calling 与完整 Agent 闭环。
- 注意：`https://agent-api.bigmodel.cn/api/agent/managed` 是智谱 **Managed Agents（云端托管 Agent）** 的地址，只提供 `/v1/agents`、`/v1/sessions`、`/v1/environments` 等路由，不是模型对话接口。本次没有接入 Managed Agents 这一产品形态；本项目保留本地 Agent 循环，因此使用同一账号下的标准模型 API。如果以后要改用 Managed Agents，需要新增 session/SSE 执行链路，详见 [P6 交接文档](docs/handoff/2026-11-23-p6-real-model.md)。

## 运行前准备

以下数据是本地课程数据，**不入库**，需要自行生成或从课程材料获取：

| 路径 | 内容 |
|---|---|
| `dataset/` | 课程材料解压目录（已 gitignore）：`test.jsonl`、`knowledge.jsonl`、`evaluation.py`、示例结果 |
| `cache/dataset/test.jsonl` | 可选：默认路径副本；也可直接用 `--dataset dataset/test.jsonl` |
| `cache/meta/samples/*.json` | 每个 idx 的快照元数据（含 `snapshot_dir`） |
| `cache/snapshots/*` | 只读仓库快照，供 Agent 工具检索 |
| `knowledge.jsonl`（仓库外或 `dataset/`） | 历史 CVE 知识库，用于构建检索索引 |

准备方式：

1. 从课程材料获取 `dataset/`（至少包含 `test.jsonl`）。CLI 可直接用 `--dataset dataset/test.jsonl`；如果要使用默认路径，复制到 `cache/dataset/test.jsonl`。
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

## 全量评测

课程测试集共 150 对、300 条，每条含 `idx`、`commit_url`、`target` 和 `func`。每次推理只给一个版本。漏洞版（`target=1`）读修复提交的父提交，修复版（`target=0`）读修复提交本身。两版独立预测后才配对计分。

| 类别 | 漏洞版预测 | 修复版预测 | 含义 |
|---|---:|---:|---|
| P-C | 1 | 0 | 两版均判对 |
| P-V | 1 | 1 | 修复版误报 |
| P-B | 0 | 0 | 漏洞版漏报 |
| P-R | 0 | 1 | 两版均判反 |

按 **P-C 对数**定基础等级。30 对及以上为 A，25–29 对为 B，20–24 对为 C，少于 20 对为 D。仅当 `P-R > P-C / 2` 时降一级，最低 D。

数据与模型就绪后，使用可断点续跑的驱动：

```powershell
python scripts/run_full_eval.py
# 数据不在默认位置时显式指定
python scripts/run_full_eval.py --dataset cache\dataset\test.jsonl
# 产物目录默认 cache\full-eval，可用环境变量或 --cache 覆盖
$env:FUNCGUARD_FULL_EVAL_CACHE = "cache\full-eval"
# 本次全量运行使用 D 盘缓存，并开启合成函数文本快照兜底
python scripts/run_full_eval.py --cache D:\FuncGuardCache --synthetic-fallback
```

- 默认缓存目录是仓库内相对路径 `cache/full-eval`；快照、元数据与运行产物写在 `<cache>/` 下，评测结果写在 `<cache>/eval/`。不要把大仓库快照放到系统盘。
- 知识索引固定从 `cache/knowledge/` 读取（可用 `FUNCGUARD_KNOWLEDGE_DIR` 覆盖），执行前需先按「知识库检索」构建。
- 按仓库分组准备快照，跑完一个仓库后删除该仓库的快照与 git 克隆，避免 TensorFlow 等大仓库占满磁盘。
- 每跑完一个样本就原子写入 `predictions.jsonl` / `status.jsonl`；中断后重跑会跳过已完成样本。
- `--synthetic-fallback`：commit 不可达或 GitHub 限流时，用数据集函数文本生成合成快照继续评测，并在状态中标记 `synthetic_snapshot`。
- 结束后自动运行 `bench.score` 与官方 `dataset/evaluation.py`，产物在 `<cache>/eval/`：
  `full-result.jsonl`、`eval-report.json`、`official-evaluation.txt`、`summary.json`、`run.log`。
  本次运行产物在 `D:\FuncGuardCache\eval\`，仓库内副本在 `cache/runs/full-eval/`。
- 进度查看：`python scripts/eval_progress.py --cache cache\full-eval`（加 `--follow` 每 10 秒刷新）。
- 课程提交文件名为 `result.jsonl`。全量运行后核对 `full-result.jsonl` 覆盖全部 300 个 idx、每行只含 `idx` 和整数 `prediction`（0 或 1），并确认 `eval-report.json` 中 `pairs_evaluated` 为 150，再按课程文件名提交副本。
- **当前状态：已完成**：300 条预测全部生成，150 对全部评分；P-C 55 / P-V 67 / P-B 20 / P-R 8，等级 A。参数与注意事项见 [P7 全量评测](docs/handoff/2026-11-24-p7-full-eval.md) 与 [P7 真实模型说明](docs/handoff/2026-11-24-p7-real-model-eval.md)。

### 2026-11-24 结果

| 口径 | 参与配对数 | P-C | P-V | P-B | P-R | 等级 |
|---|---:|---:|---:|---:|---:|---|
| 全量（含合成兜底） | 150 | 55 | 67 | 20 | 8 | A |
| 仅真实仓库快照 | 94 | 36 | 41 | 13 | 4 | A |

- 300 条预测全部生成，官方 `dataset/evaluation.py` 与 `bench.score` 结果一致，跳过组数 0。
- 真实快照 194 条；合成快照 106 条，主要来自 TensorFlow（65）、mruby（12）、linux（8）等因 GitHub 429/commit 不可达的仓库。
- 结果副本：`cache/runs/full-eval/`（`cache/` 不入库）；完整产物在 `D:\FuncGuardCache\eval\`。
- 详见 [P7 交接文档](docs/handoff/2026-11-24-p7-full-eval.md)。

## 知识库检索

知识库仅使用课程提供的历史 CVE 数据，可成对使用历史漏洞版和修复版，引用漏洞描述、修复提交说明、CWE 和项目元信息，也可用 LLM 整理这些材料。课程不限定索引形式，本项目使用 BM25。检测 Agent 禁止联网搜索或引入额外安全知识，仓库获取限于 `commit_url` 对应的指定版本。知识库文件保留在本地，构建产物写入 `cache/knowledge/`。

```powershell
$env:FUNCGUARD_KNOWLEDGE_PATH = "<knowledge.jsonl 路径>"
python -m knowledge.audit_leakage --knowledge $env:FUNCGUARD_KNOWLEDGE_PATH --threshold 0.5
python -m rag.build_index --knowledge $env:FUNCGUARD_KNOWLEDGE_PATH
python -m knowledge.audit_leakage --cards --threshold 0.5
```

- 审计必须先跑：build 会剔除与任一测试样本函数包含度 ≥0.5 的知识记录，以及测试函数
  注释提到、知识库同编号 CVE 的记录；当前从 7,574 条唯一卡片中剔除 77 条，入索引 7,497 条。
- 建完索引后再跑一次卡片审计，残余应为 0 对；`--include-leaky` 只用于研究，评测/演示禁用。
- 单次构建约 16 s，缓存约 37 MB；重建索引后需重启服务。
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
| `scripts/` | 运维/评测脚本：`run_full_eval.py` 全量评测驱动 |
| `.env.example` | 模型与代理配置模板；复制为 `.env.local` 后生效 |
| `cache/` | 本地缓存与运行产物，不入库 |

## 交接文档

各里程碑的交接文档与阅读顺序见 [docs/handoff/README.md](docs/handoff/README.md)。

## 约定

给 agent 看的文件统一放到 `.agents/` 目录里。
