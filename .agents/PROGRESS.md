# 已经取得的进展和完成的任务

> 使用约定：开工前在 TODO.md 认领任务并注明认领人与日期；收工前更新本文件状态，并在 docs/handoff/ 写交接文档。

> 格式
> ## 任务标题
>
> 完成人 完成日期
>
> 任务说明
>
> 需要交代的事项

## 建立仓库与创建初始文件

朱辰 2026.11.22

建立仓库，创建初始文件，包括README.md、.agents/目录和相关文件

当前所有文档都只是暂定版本，需要进一步讨论和修改

## 接入 GLM-5.3-Flash 真实模型

赵志浩 2026.11.23

完成 README「真实模型接入」：接入智谱 GLM-5.3-Flash（OpenAI 兼容接口），
新增本地 `.env.local` 配置加载与 provider 参数，更新 CLI 自检、README 与 P6 交接文档。

需要交代的事项：

- 仓库不包含 API key；真实 key 只放在本机 `.env.local`（已 gitignore）。未配置 key 时仍回退到确定性 stub。
- 真实模型接入任务已完成：模型接口为 `https://open.bigmodel.cn/api/paas/v4`，模型为 `glm-5.3-flash`，已跑通 function calling 与完整 Agent 闭环。`https://agent-api.bigmodel.cn/api/agent/managed` 是 Managed Agents（云端托管 Agent）接口，不是模型对话接口；本次没有接入 Managed Agents 这一产品形态，但不影响模型接入任务的完成。
- 已完成 provider 直连、Agent 闭环、CLI 编排和 `/api/health` 冒烟验证；
- 本次改动已于 2026.11.23 提交为 `7954aa4`，直接推送到 `main`（未走 PR）；提交内容不包含 `.env.local` 与本机 `CLAUDE.md`。
- 300 条全量评测已完成，结果见 `docs/handoff/2026-11-24-p7-full-eval.md` 与 README。
- 2026.11.24 补记：全量评测已完成，300 条预测全部参与评分；此前“进行中/未核实”的状态作废。

## 基础链路（P1 / P2）

2026.11.22，分支 `feat/p1-foundation`、`feat/p2-snapshot`（均已合并 `main`）

- P1：仓库骨架、六个 JSON 契约、评测基线。
- P2：commit URL 解析、原生 git 快照、函数定位 v1；用 6 条样本验证，43 仓库全量快照未跑。

交接文档：`docs/handoff/2026-11-22-p1-foundation.md`、`2026-11-22-p2-snapshot.md`。

## Agent 闭环与界面（P4 / P5）

2026.11.22，分支 `feat/p4-agent-e2e`、`feat/p5-ui`（均已合并 `main`）

- P4：自主循环、只读工具、证据门控、CLI；当时为 3 个工具、stub 口径。
- P5：Vue3 单页 + FastAPI，单条/批量/报告/轨迹接口，`web/dist` 入库。
- 结论均来自 stub provider，只验证链路，不代表真实检测能力。

## 知识库 / RAG 与严格泄漏隔离（P3）

2026.11.23，分支 `feat/p3-rag`（PR #7）、`fix/leakage-strict`（PR #8），均已合并 `main`

- P3：泄漏审计、知识卡片、纯 Python BM25、`GET /api/knowledge/search`、Agent 工具 `search_knowledge`、前端检索面板。
- 泄漏阈值由 0.8 收紧到 0.5：全量枚举 178 对 ≥0.5 配对，剔除 77 条（近似 75 + CVE 文本提及 2），最终 **7,497 条**入索引；索引卡片复核残余 0 对（最高包含度 0.4941）。
- 检索自测（40 个固定案例）收紧前后一致；正例 100% 是自检索上界，不代表泛化能力。

交接：`docs/handoff/2026-11-23-p3-knowledge-rag.md`、`2026-11-23-p3-leakage-strict.md`；审计报告 `docs/p3-knowledge-leakage-audit.md`。

## 交接文档与口径维护（P7）

2026.11.24，分支 `docs/p7-handoff`

- 新增 `docs/handoff/2026-11-24-p7-real-model-eval.md`：GLM 接入方式、provider 配置、全量评测驱动用法与当前未完成项。
- 统一 README 与 `docs/handoff/README.md` 的全量评测口径为“驱动已就绪、尚未执行、无结果文件”。
- 修正 README 中 `max_tokens` / 总超时 / 单请求超时，与 `backend/config.py` 默认值对齐（16384 / 600 s / 180 s）。
- 去掉 `scripts/run_full_eval.py`、`scripts/eval_progress.py` 与 README 中写死的本机盘符评测缓存路径，改为环境变量 `FUNCGUARD_FULL_EVAL_CACHE`，缺省落到仓库内 `cache/full-eval`。
- 重排 `.agents/TODO.md` 为可认领清单。

当前 `main`：见仓库 HEAD（2026-11-24 已合并 P7 文档与单元测试基线）。

2026.11.24 补记：全量评测已完成，结果见 `docs/handoff/2026-11-24-p7-full-eval.md`；本节“尚未执行”的口径已被该文档覆盖。

## 课程题目与验收文档

2026.11.24，Codex。依据国科大《高级软件工程》课件更新 README、根目录 AGENTS.md 和测试说明。已按课件页码核对图形输入、Agent 决策与检索顺序、样本隔离和配对评分。评分测试 23 项通过。课程输入与流程的待办见任务 7，全量评测仍由原任务跟踪。见[课程要求与实现对照](../docs/handoff/2026-11-24-course-requirements.md)。

2026.11.24 补记（Codex）：将课件的检测流程、隔离要求、报告字段和配对评分整理为[课程要求速查](../ref/3.%20函数漏洞检测智能体工具.md)，README 与根目录 AGENTS.md 均提供入口。

## 完成 300 条全量评测

赵志浩 2026.11.24

使用 GLM-5.3-Flash 在课程测试集上完成 300 条（150 对）全量评测，
新增断点续跑驱动 `scripts/run_full_eval.py` 与进度查看脚本 `scripts/eval_progress.py`，
更新 README、P7 交接文档与交接索引。

结果：P-C 55 / P-V 67 / P-B 20 / P-R 8，最终等级 A；官方 `dataset/evaluation.py`
与 `bench.score` 结果一致，150 对全部参与评分。

需要交代的事项：

- 300 条中 194 条使用真实仓库快照，106 条因 GitHub 429 限流或 commit 不可达使用合成函数文本快照；状态文件里以 `synthetic_snapshot=True` 标记。
- 仅真实快照子集为 94 对：P-C 36 / P-V 41 / P-B 13 / P-R 4，等级 A；样本量较小，仅作参考。
- 合成快照主要集中在 TensorFlow（65）、mruby（12）、linux（8）等仓库；后续网络/凭据条件允许时可重跑这些仓库替换为真实快照。
- 结果副本在 `cache/runs/full-eval/`（不入库），完整产物在 `D:\FuncGuardCache\eval\`。
- 本轮为单次运行，未做多随机种子重复与逐样本人工复核；P-C/P-R 仍可能存在模型误判。
## 课程图形输入：手工样本与上传 jsonl（任务 7）

lukezhen 2026.11.25

补齐图形界面的两条课程输入：手工输入函数 + 修复提交链接、上传 `.jsonl` 批量检测。
新增 `backend/services/adhoc_service.py`、`POST /api/runs/manual`、`POST /api/batches/upload`；
界面新增对应区域，并把「准备快照」阶段显示给用户。两条通路都不读测试集、不参与全量评测，
按数据集评分的 `run_service` 路径未改。

需要交代的事项：

- 版本由用户显式选择（修复前 = 父提交 / 修复后 = 修复提交本身），模型仍只收到一个版本快照。
- 手工样本的 `idx` 可留空，报告记为 0 并标 `provided_idx=false`；上传样本 `idx` 必填且唯一，单次上限 200 条。
- 未改提示词顺序；已提交的 300 条评测结果对应改动前的提示词，若要改顺序需单独立项并重跑。
- 验证：`pytest -q` 112 passed / 1 skipped / 1 xfailed（新增 18 + 4 条）；接口冒烟通过。
- 未完成：真实模型 + 真实 GitHub 仓库的浏览器端到端演示（本机无 `.env.local`），以及检索顺序与版本身份泄漏两项课程检查。
- 交接：`docs/handoff/2026-11-25-p8-manual-input.md`，索引已登记；PR #13 已推送。