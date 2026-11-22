# 交接：P1 地基（仓库骨架 + 契约 + 评测基线）

- 分支：`feat/p1-foundation`
- 日期：2026-11-22
- 范围：只做“可提交、可验证”的地基，不包含 P2–P5 业务实现

## 做了什么

1. 建立目录骨架：`bench/`、`repo_service/`、`repo_tools/`、`knowledge/`、`rag/`、`backend/`、`web/`、`shared/contracts/`、`cache/`。
2. 定义六个 JSON 契约（JSON Schema 2020-12）：
   - `Sample`：测试集单条样本。
   - `ResultRecord`：`result.jsonl` 单条结果，只允许 `idx` + `prediction`。
   - `EvalReport`：一次评测的 P-C/P-V/P-B/P-R 与等级。
   - `RepoSnapshot`：按版本导出的只读快照元数据。
   - `KnowledgeHit`：知识库检索命中。
   - `TraceEvent`：Agent 运行轨迹事件。
3. 实现可运行的评测地基：
   - 测试集加载与严格校验（300 条、150 对、整数 `idx/target`）。
   - `result.jsonl` 生成器，支持全 0 / 全 1 两种基线策略。
   - 评分脚本，统计口径与官方 `evaluation.py` 一致，并计算等级与 P-R 降级。
4. 本地交叉验证：全 0 → 150 P-B；全 1 → 150 P-V；自研脚本与官方脚本结果完全一致。
5. `cache/` 写入 `.gitignore`，测试集、快照、基线输出不入库。

## 动了哪些文件/目录

| 路径 | 变更 | 说明 |
|---|---|---|
| `.gitignore` | 修改 | 末尾追加 `cache/`，仅新增忽略规则，未改动原有内容 |
| `shared/contracts/sample.schema.json` | 新增 | 测试集样本契约 |
| `shared/contracts/result_record.schema.json` | 新增 | 结果记录契约 |
| `shared/contracts/eval_report.schema.json` | 新增 | 评测报告契约 |
| `shared/contracts/repo_snapshot.schema.json` | 新增 | 仓库快照契约 |
| `shared/contracts/knowledge_hit.schema.json` | 新增 | 检索命中契约 |
| `shared/contracts/trace_event.schema.json` | 新增 | 轨迹事件契约 |
| `bench/__init__.py` | 新增 | bench 包入口 |
| `bench/dataset.py` | 新增 | 测试集读取、校验、相邻配对 |
| `bench/load_dataset.py` | 新增 | 数据集摘要 CLI |
| `bench/write_result.py` | 新增 | `result.jsonl` 生成器 |
| `bench/score.py` | 新增 | P-C/P-V/P-B/P-R 与等级计算 |
| `bench/fixtures/.gitkeep` | 新增 | 固定回归集占位（尚未放入 20 对） |
| `repo_service/.gitkeep` | 新增 | P2 目录占位 |
| `repo_tools/.gitkeep` | 新增 | P2 目录占位 |
| `knowledge/.gitkeep` | 新增 | P3 目录占位 |
| `rag/.gitkeep` | 新增 | P3 目录占位 |
| `backend/.gitkeep` | 新增 | P4 目录占位 |
| `web/.gitkeep` | 新增 | P5 目录占位 |
| `docs/handoff/2026-11-22-p1-foundation.md` | 新增 | 本交接文档 |

未改动：`README.md`、`AGENTS.md`、`.agents/` 下所有既有文档。

## 怎么验证

前置：把课程提供的 `test.jsonl` 复制到 `cache/dataset/test.jsonl`（该目录已被忽略）。以下命令均在仓库根目录执行。

```powershell
# 1. 数据集校验
python -m bench.load_dataset --dataset cache/dataset/test.jsonl
# 期望：记录数 300、唯一 idx 300、配对数 150、target=1/0 各 150

# 2. 生成两种基线
python -m bench.write_result --strategy all0 --output cache/baseline/all0.jsonl
python -m bench.write_result --strategy all1 --output cache/baseline/all1.jsonl

# 3. 自研评分
python -m bench.score --dataset cache/dataset/test.jsonl --result cache/baseline/all0.jsonl --json-out cache/baseline/all0.report.json
python -m bench.score --dataset cache/dataset/test.jsonl --result cache/baseline/all1.jsonl --json-out cache/baseline/all1.report.json

# 4. 与官方脚本交叉验证（把 evaluation.py 也放进 cache/dataset/）
python cache/dataset/evaluation.py --dataset cache/dataset/test.jsonl --result cache/baseline/all0.jsonl
python cache/dataset/evaluation.py --dataset cache/dataset/test.jsonl --result cache/baseline/all1.jsonl
```

本次实测结果：

| 结果文件 | P-C | P-V | P-B | P-R | 已评测配对 | 基础等级 | 最终等级 |
|---|---|---|---|---|---|---|---|
| `all0.jsonl` | 0 | 0 | 150 | 0 | 150 | D | D |
| `all1.jsonl` | 0 | 150 | 0 | 0 | 150 | D | D |

自研 `bench.score` 与官方 `evaluation.py` 的四类计数完全一致。基线等级为 D 属正常现象（P-C=0），不代表脚本错误。

## 没做什么

- 未实现 P2：URL 解析、原生 git 克隆/取父提交/导出快照、函数定位、仓库工具。
- 未实现 P3：知识库审计、清洗、卡片、向量索引、检索 API。
- 未实现 P4：FastAPI 服务、Agent 图、工具注册、SSE 轨迹、批量任务。
- 未实现 P5：Vue3 页面、实时轨迹、批量上传与报告展示。
- 未提交依赖清单（`requirements.txt` / `package.json`）与 CI。
- 未把 `test.jsonl`、`knowledge.jsonl`、任何快照提交进仓库。
- 未建立 20 对固定回归集，`bench/fixtures/` 目前只有占位文件。

## 下一步 TODO

1. P1：补 `bench/fixtures/subset20.jsonl` 与分层统计（项目 / 函数长度 / CWE）。
2. P1：把六个契约接入校验脚本，生成结果后先校验再评分。
3. P2：先打通 3 个仓库（ImageMagick / gpac / tensorflow 各 1 个 commit）的原生 git 快照链路。
4. P4：起 FastAPI 空壳与 mock SSE，让 P5 可以对着 `TraceEvent` 契约开发。
5. P5：起 Vue3 工程与运行页，先用 mock 轨迹，不等后端。

## 下一步可认领的任务

> 依赖状态说明：**已就绪** = 现在拉分支即可开工；**部分就绪** = 可先用 mock/契约开工，真实接口稍后接；**未就绪** = 需要等待上游产出。

### P1：数据与评测

| 任务 | 具体内容 | 涉及目录/文件 | 依赖是否就绪 | 预估 |
|---|---|---|---|---|
| 20 对固定回归集 | 从 `test.jsonl` 选 20 对覆盖不同仓库与长度区间，固定为 `bench/fixtures/subset20.jsonl`，并提供快速评分命令 | `bench/fixtures/subset20.jsonl`、`bench/score.py` | 已就绪 | 3-4 人时 |
| 分层统计 | 在评分结果中增加按仓库、函数长度分桶、CWE 的 P-C/P-V/P-B/P-R 统计 | `bench/score.py`、`shared/contracts/eval_report.schema.json`（需评审后扩展） | 已就绪 | 6-8 人时 |
| 契约校验脚本 | 读取六个 JSON Schema，对 `test.jsonl`、`result.jsonl`、`EvalReport` 做校验；无第三方依赖时可用轻量校验或引入 `jsonschema` | `bench/validate_contracts.py`、`shared/contracts/` | 已就绪 | 4-5 人时 |
| 泄漏审计 v1 | 检查测试集 idx/cve/commit/函数哈希是否进入知识库与索引，输出命中清单 | `bench/leak_audit.py`、`cache/` | 部分就绪（需 P3 产出知识卡片/索引后跑全量） | 8-10 人时 |
| 实验记录表 | 维护模型、prompt 版本、top-k、分数、耗时、token 的记录表 | `reports/experiment_log.csv`（需新建 `reports/`） | 已就绪 | 2-3 人时 |

### P2：仓库与代码智能

| 任务 | 具体内容 | 涉及目录/文件 | 依赖是否就绪 | 预估 |
|---|---|---|---|---|
| URL 解析 | 支持 GitHub 标准 commit URL 与 `git.qemu.org` 非标准 URL，解析 owner/repo/sha；写单元测试 | `repo_service/urls.py` | 已就绪（`Sample` 契约已定） | 4-6 人时 |
| git 客户端封装 | 原生 git：`ls-remote`、blobless clone、按 sha fetch、取第一父提交、`archive` 导出；统一超时与重试；不依赖 `gh` | `repo_service/git_client.py` | 已就绪 | 8-10 人时 |
| 3 仓库试点快照 | 选 ImageMagick、gpac、tensorflow 各 1 个 commit，验证 target=1 取父提交、target=0 取本身，快照目录无 `.git` | `repo_service/snapshot.py`、`cache/git/`、`cache/snapshots/` | 已就绪（需先完成 git 客户端） | 10-12 人时 |
| 批量快照 | 43 仓库 / 150 commit 批量准备，并发 2-3，失败重试，输出 `RepoSnapshot` 元数据 | `repo_service/prepare_all.py`、`cache/meta/` | 部分就绪（需试点通过） | 10-14 人时 |
| 函数定位 v1 | 无 `file_name` 情况下用完整文本 → 规范化 → 签名+大括号 → 函数名+关键语句逐级匹配，输出置信度 | `repo_service/locator.py` | 未就绪（依赖快照） | 12-16 人时 |
| 只读仓库工具 | `grep`、`find`、`read_file`、`get_function` 等，含路径白名单、大小/条数/超时限制 | `repo_tools/*.py` | 未就绪（依赖快照） | 8-12 人时 |

### P3：知识工程/RAG

| 任务 | 具体内容 | 涉及目录/文件 | 依赖是否就绪 | 预估 |
|---|---|---|---|---|
| 知识库审计 | 统计字段、项目、CWE、重复 idx、`file_name` 为空比例，输出审计报告 | `knowledge/audit.py`、`reports/` | 已就绪（`knowledge.jsonl` 在本地） | 6-8 人时 |
| 测试集黑名单 | 从 P1 获取测试集 idx/cve/commit/函数哈希，建立禁止入库清单 | `knowledge/blacklist.py`、`cache/` | 部分就绪（需 P1 提供清单格式） | 3-4 人时 |
| 清洗与知识卡片 | 去重、统一字段、生成结构化卡片（项目/CVE/CWE/签名/漏洞模式/修复模式摘要） | `knowledge/clean.py`、`knowledge/build_cards.py`、`knowledge/cards.jsonl` | 部分就绪（需黑名单） | 10-14 人时 |
| 索引与检索 API | 向量索引 + BM25 兜底，提供 `Retriever.search()`，返回 `KnowledgeHit` | `rag/build_index.py`、`rag/retriever.py`、`rag/index/` | 未就绪（依赖卡片） | 12-16 人时 |
| 检索评估 | 30-50 个查询，人工判断 top-1/top-5，对比代码/描述/混合三种表示 | `reports/retrieval_eval.md`、`rag/` | 未就绪（依赖检索 API） | 8-10 人时 |

### P4：Agent 与后端

| 任务 | 具体内容 | 涉及目录/文件 | 依赖是否就绪 | 预估 |
|---|---|---|---|---|
| FastAPI 空壳 | `/health`、运行接口骨架、统一响应结构、JWT/鉴权占位（按课程要求再定） | `backend/app/main.py`、`backend/app/api/` | 已就绪（`TraceEvent` 契约已定） | 5-7 人时 |
| mock SSE 轨迹 | 按 `TraceEvent` 推送假事件，供 P5 联调；支持 run_start → knowledge_search → repo_tool → report → run_end | `backend/app/api/runs.py`、`backend/app/services/` | 已就绪 | 4-6 人时 |
| Agent 图骨架 | 工具注册表、状态机/图节点、最大步数与超时；工具先接 mock，再换 P2/P3 实现 | `backend/app/agent/`、`backend/prompts/` | 部分就绪（可先 mock） | 12-16 人时 |
| 单条检测闭环 | 输入 `Sample` → 检索/工具 → 判定 → 输出报告四项，落 `TraceEvent` | `backend/app/services/`、`shared/contracts/` | 未就绪（依赖 P2/P3 接口） | 12-16 人时 |
| 批量任务 API | 接收 `.jsonl`、逐条运行、进度查询、导出 `result.jsonl`（复用 `bench.write_result` 的格式） | `backend/app/api/`、`bench/` | 部分就绪（依赖单条闭环） | 8-10 人时 |

### P5：前端与交付

| 任务 | 具体内容 | 涉及目录/文件 | 依赖是否就绪 | 预估 |
|---|---|---|---|---|
| Vue3 工程初始化 | Vite + Vue3 + Pinia + axios 拦截器；统一错误码处理 | `web/package.json`、`web/src/main.ts`、`web/src/api/`、`web/src/stores/` | 已就绪 | 5-7 人时 |
| 运行页 + 实时轨迹 | 单条函数输入、启动检测、按 `TraceEvent` 渲染时间线（检索记录、仓库读取记录） | `web/src/views/RunView.vue`、`web/src/components/TraceTimeline.vue` | 部分就绪（先用 mock SSE） | 12-16 人时 |
| 报告展示 | 展示 idx、检测结果、判定理由、分析记录四项 | `web/src/views/ReportView.vue`、`web/src/components/` | 部分就绪（契约已定） | 6-8 人时 |
| 批量页 | 上传 `.jsonl`、进度、失败重试、导出 `result.jsonl` | `web/src/views/BatchView.vue`、`web/src/api/` | 未就绪（依赖 P4 批量 API） | 10-12 人时 |
| 交付材料 | README 启动说明、演示脚本/录屏、答辩素材 | `README.md`、`docs/`、`demo/` | 未就绪（依赖可运行版本） | 8-10 人时 |

## 踩坑与禁区

1. **网络**：本机直连 `github.com:443` TLS 握手失败；需走本地代理 `127.0.0.1:7897`。本次仅在命令上临时加 `-c http.proxy=...`，**未写入仓库配置**，避免污染他人环境。
2. **凭证**：token 只存放在 `E:\软件工程\.git-credentials-github`；仓库级 `credential.helper` 指向该文件，并在其前面加了空值以覆盖全局 `store`，防止写入 C 盘 `~/.git-credentials`。任何文档、代码、提交信息都不得出现明文 token。
3. **测试数据**：`test.jsonl`、`knowledge.jsonl`、仓库快照、基线输出全部放 `cache/`，已被 `.gitignore` 忽略；不要为了“方便”提交进仓库。
4. **评测口径**：按测试集**相邻两条**配对，身份由 `target` 决定，不是按 `commit_url` 分组。写任何统计逻辑前先读 `bench/dataset.py`。
5. **基线等级**：全 0 / 全 1 的最终等级都是 D，这是 P-C=0 的正常结果，不要误判为脚本 bug。
6. **Python 版本**：本机为 Python 3.9.13。不要使用 `match`、运行时的 `str | None` 等 3.10+ 语法；当前代码已用 `from __future__ import annotations` 规避注解求值问题。
7. **分支纪律**：只允许向 `feat/p1-foundation` 提交；禁止直接推 `main`，禁止 force push。
8. **契约变更**：修改 `shared/contracts/` 下任何字段都属于跨模块变更，必须单独 PR，并通知 P1/P4/P5。
