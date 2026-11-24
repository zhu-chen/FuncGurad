# P7 真实模型配置与全量评测驱动

- 日期：2026-11-24
- 基线：`main` @ `d00d656`（写文档时的 main；GLM 接入与评测驱动由 2026-11-23 的两个提交直接推入 main）
- 分支：`docs/p7-handoff`（只改文档、脚本默认路径与 README，不动 Agent 循环与契约）
- 状态：真实模型已接入；全量评测**驱动已就绪，状态待确认（队友报告进行中，未经核实）；产物只写 `cache/`、不入库，看不到结果属正常**

## 1. 一分钟接手

| 需要准备 | 来源 | 放哪里 | 自检 |
|---|---|---|---|
| 课程数据 `test.jsonl` | 课程材料 | `dataset/test.jsonl` 或 `cache/dataset/test.jsonl` | `python -m backend.cli check` |
| 模型 key | 智谱开放平台（或任意 OpenAI 兼容服务） | 本机 `.env.local` 的 `FUNCGUARD_LLM_API_KEY` | `check` 显示 `is_stub=False` |
| 知识索引 | `python -m rag.build_index --knowledge <knowledge.jsonl>` | `cache/knowledge/` | `/api/knowledge/search` 返回 200 |
| 磁盘 | 自备 | 全量评测默认 `<repo>/cache/full-eval` | 单仓库快照可达数十 GB |

缺 key 时 provider 自动回退确定性 stub，`run_full_eval.py` 会拒绝执行（退出码 2），
只有显式加 `--allow-stub` 才会跑 stub 基线。缺知识索引时评测照常开始，但
`search_knowledge` 工具会失败。

## 2. GLM 接入方式

- 接入的是智谱开放平台的**标准模型 API**（OpenAI Chat Completions 兼容），
  不是 `agent-api.bigmodel.cn/api/agent/managed` 的 Managed Agents 产品。
- 本地 Agent 循环保持不变：模型只负责决定调用哪个只读工具并给出结论，
  证据门控、轨迹与 `result.jsonl` 仍由本项目代码控制。
- provider 实现：`backend/providers/openai_compat.py`，走 SSE 流式 + function calling。
- GLM-5.3-Flash 会先输出 `reasoning_content` 再输出结论；工具调用后必须把上一轮
  `reasoning_content` 原样回传（已在 Agent 循环中处理）。
- 补充事实（2026-11-24）：真实 provider 已用 DeepSeek 验证过一对样本（idx 194989 / 218785），
  结果为 P-R，两版均判反；该结果只说明当前提示词与上下文下存在方向性问题，
  不构成检测能力结论；产物只在 `cache/`。

## 3. provider 配置项

| 环境变量 | 默认值 | 说明 |
|---|---|---|
| `FUNCGUARD_LLM_PROVIDER` | `auto` | `auto` 有 key 用真实模型、无 key 回退 stub；`zhipu` / `glm` / `openai` 缺 key 直接报错；`stub` 强制占位 |
| `FUNCGUARD_LLM_API_KEY` | 空 | 首选 key 变量；也识别 `ZAI_API_KEY` / `ZHIPUAI_API_KEY` / `OPENAI_API_KEY` |
| `FUNCGUARD_LLM_BASE_URL` | `https://open.bigmodel.cn/api/paas/v4` | 使用 `OPENAI_API_KEY` 时默认改为 `https://api.openai.com/v1` |
| `FUNCGUARD_LLM_MODEL` | `glm-5.3-flash` | base_url 含 `bigmodel.cn` 时用智谱默认，否则 `gpt-4o-mini` |
| `FUNCGUARD_LLM_MAX_TOKENS` | `16384` | 单次输出上限；长推理会吃掉额度 |
| `FUNCGUARD_LLM_TEMPERATURE` | `0.0` | 检测任务建议保持 0 |
| `FUNCGUARD_LLM_STREAM` | `1` | 建议保持开启，避免长推理读超时 |
| `FUNCGUARD_LLM_REASONING_EFFORT` | `low` | `low` / `high` / `max`，GLM 不能关闭 thinking |
| `FUNCGUARD_LLM_RETRIES` | `2` | 请求重试次数 |
| `FUNCGUARD_LLM_TIMEOUT` | `180` | 单次模型请求超时（秒） |
| `FUNCGUARD_AGENT_TIMEOUT` | `600` | 单样本总超时（秒）；全量驱动默认覆盖为 900 |
| `FUNCGUARD_AGENT_MAX_STEPS` | `10` | 单样本最大步数 |
| `FUNCGUARD_LLM_PROXY` | 空 | 模型请求代理；未设时回退 `FUNCGUARD_GIT_PROXY` |
| `FUNCGUARD_GIT_PROXY` | 空 | git 快照链路代理；仓库不内置任何代理默认值 |

优先级：真实环境变量 > `.env.local` > `.env`。三者都不入库。

## 4. 全量评测驱动 `scripts/run_full_eval.py`

```powershell
python scripts/run_full_eval.py
python scripts/run_full_eval.py --dataset cache\dataset\test.jsonl
python scripts/run_full_eval.py --only-repos ImageMagick__ImageMagick6
```

| 参数 | 默认值 | 说明 |
|---|---|---|
| `--dataset` | `dataset/test.jsonl` | 测试集；课程数据常放在 `cache/dataset/test.jsonl`，需显式指定 |
| `--cache` | `cache/full-eval` | 快照、元数据、运行与评测产物根目录；可用 `FUNCGUARD_FULL_EVAL_CACHE` 覆盖 |
| `--workers` | `4` | 快照准备并发数 |
| `--prepare-timeout` | `7200` | 单仓库快照准备超时（秒） |
| `--max-steps` | `10` | 单样本最大步数 |
| `--timeout` | `900` | 单样本总超时（秒） |
| `--request-timeout` | `180` | 单次模型请求超时（秒） |
| `--reasoning-effort` | `low` | `low` / `high` / `max` |
| `--only-repos` | 空 | 只跑指定 `owner__repo`，逗号分隔，用于小范围试跑 |
| `--keep-snapshots` | 关 | 跑完不删除该仓库快照（默认删除以控制磁盘） |
| `--allow-stub` | 关 | 允许 stub provider；默认遇到 stub 直接退出（码 2） |

行为：

1. 按仓库分组准备快照，跑完一个仓库后删除该仓库的快照与 git 克隆。
2. 每完成一个样本就原子写 `predictions.jsonl` / `status.jsonl`，中断后可续跑。
3. 结束后运行 `bench.score` 与官方 `dataset/evaluation.py`。

产物都在 `<cache>/eval/`：

| 文件 | 内容 |
|---|---|
| `predictions.jsonl` | `{"idx", "prediction"}` 逐样本预测 |
| `status.jsonl` | 逐样本状态、耗时、错误原因 |
| `full-result.jsonl` | 合成后的 `result.jsonl` |
| `eval-report.json` | P-C / P-V / P-B / P-R 与等级 |
| `official-evaluation.txt` | 官方评测脚本输出 |
| `summary.json` | 汇总指标 |
| `run.log` | 运行日志 |

进度查看：`python scripts/eval_progress.py --cache cache\full-eval`（`--follow` 每 10 秒刷新）。

## 5. 当前完成度

| 项 | 状态 |
|---|---|
| GLM-5.3-Flash 接入、function calling、SSE 流式 | 已完成，并有合成样例与 ImageMagick6 样本冒烟 |
| provider 配置、`.env.local` 加载、CLI 自检 | 已完成 |
| 全量评测驱动与断点续跑 | 已完成 |
| 300 条全量评测 | **状态待确认（队友报告进行中，未经核实）**；驱动已就绪，产物只写 `cache/`、不入库，P-C/P-V/P-B/P-R 与等级暂无数字 |
| 向量 / 混合检索 | 未实现，仍为纯 BM25 |
| 单元测试基线 | 已完成并合并（2026-11-24） |

本机（2026-11-24）没有 `.env.local`，也没有评测产物目录，因此本机无法给出真实模型评测数字；
`cache/` 不入库，其他机器上的产物在这里看不到属正常，不要据本机情况推断整体进度。
不要引用 P6 的合成样例结论代替全量结果。

## 6. 用你自己的 key

```powershell
Copy-Item .env.example .env.local
# 编辑 .env.local，至少填 FUNCGUARD_LLM_API_KEY
python -m backend.cli check     # 期望 is_stub=False, model=glm-5.3-flash
```

- 也可以不写文件，直接设真实环境变量（优先级更高）：
  `$env:FUNCGUARD_LLM_API_KEY = "<你的 key>"`。
- 换其他 OpenAI 兼容服务：同时设 `FUNCGUARD_LLM_BASE_URL` 与 `FUNCGUARD_LLM_MODEL`；
  不要把 key 或内网地址写进仓库。
- 回退 stub：`$env:FUNCGUARD_LLM_PROVIDER = "stub"`。

## 7. 注意事项与环境坑

1. **费用**：全量 300 条会持续调用真实模型，先跑 `--only-repos` 小范围试跑再放开。
2. **长推理**：GLM-5.3-Flash 不能关闭 thinking；额度或超时不够会表现为空 content、
   请求超时或步数耗尽，优先检查 `max_tokens` / `timeout` / `request-timeout`。
3. **磁盘**：快照是完整源码树，TensorFlow 等大仓库可能数十 GB；评测缓存默认在仓库内，
   磁盘紧张时用 `--cache` 指到空间充足的盘。
4. **知识索引**：固定从 `cache/knowledge/` 读取（或 `FUNCGUARD_KNOWLEDGE_DIR`），
   与 `--cache` 无关；评测前先构建，否则 `search_knowledge` 不可用。
5. **代理**：需要时显式设 `FUNCGUARD_GIT_PROXY` / `FUNCGUARD_LLM_PROXY`，仓库不内置。
6. **中断续跑**：直接重跑同一条命令，已完成样本会跳过；不要手动改 `predictions.jsonl`。
7. **数据自检**：`python -m backend.cli check` 退出码非 0 时先补齐数据，再开始评测。

## 8. 禁区

1. 不把 `shared/contracts/` 的字段与语义改掉；`result.jsonl` 只能有 `idx` 与 `prediction`。
2. 不提交 API key、`.env.local`、`.env`，也不在文档/日志里打印 key。
3. 不提交 `cache/`、`dataset/`（测试集与知识库）与 `web/node_modules/`。
4. 不放宽泄漏阈值（当前 0.5），不把测试集或知识库内容写进评测提示词。
5. 不在 Agent 循环里写死工具调用顺序；顺序应由模型决定。
6. 不在代码、脚本默认值或文档里写本机盘符路径，路径一律用仓库相对写法或环境变量。
7. 不用 stub 结果冒充真实检测能力；未跑完的评测不要写具体指标。

## 9. 复现与验证

```powershell
python -m backend.cli check
python scripts/run_full_eval.py --only-repos ImageMagick__ImageMagick6
python scripts/eval_progress.py --cache cache\full-eval
```

小范围跑通后再执行全量，并把 `summary.json` 的指标与本文件第 5 节同步更新。
