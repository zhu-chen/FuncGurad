# P6 真实模型接入（GLM-5.3-Flash）

- 日期：2026-11-23
- 状态：已完成本地接入与冒烟验证；全量评测仍未执行
- 关联：README「真实模型配置」、「当前进度」

## 1. 目标

把 README 里「真实模型接入：未接入」补齐，让 Agent 循环在配置 key 后调用真实模型，
而不是始终使用确定性 stub。

## 2. 结论：接入标准 OpenAI 兼容模型 API

最终接入的是智谱开放平台的 **标准模型 API**（OpenAI Chat Completions 兼容）：

| 配置 | 值 |
|---|---|
| Base URL | `https://open.bigmodel.cn/api/paas/v4` |
| Model | `glm-5.3-flash` |
| 协议 | OpenAI Chat Completions + function calling |
| Key 环境变量 | `FUNCGUARD_LLM_API_KEY`（兼容 `ZAI_API_KEY` / `ZHIPUAI_API_KEY` / `OPENAI_API_KEY`） |

同一个 API Key 同时可访问该模型接口；已实测普通对话、function calling 与
多轮 tool 消息回传均正常。

因此 README 的「真实模型接入」任务**已经完成**。第 3 节说明的是为什么没有采用
Managed Agents 这一产品形态，而不是模型没有接入。

## 3. 为什么没有直接使用 `agent-api.bigmodel.cn/api/agent/managed`

该地址是智谱 **Managed Agents（云端托管 Agent）** 的基础地址，路由是
`/v1/agents`、`/v1/environments`、`/v1/sessions`、`/v1/sessions/{id}/events` 等；
它没有 `/chat/completions`，平台会自己在云端沙箱里驱动 Agent 循环。
本项目已有本地 Agent 循环、只读仓库工具和证据门控，直接接入模型 API 可以复用
这些能力；改用 Managed Agents 需要新增 session/SSE 执行链路，属于另一条架构路线，
不在本次范围内。

## 4. 改动清单

| 文件 | 改动 |
|---|---|
| `repo_service/env.py` | 新增：启动时读取仓库根目录 `.env.local` / `.env`，真实环境变量优先，不打印值 |
| `backend/config.py` | 默认 base/model 改为智谱 + `glm-5.3-flash`；支持多个 key 环境变量；新增 `max_tokens`；总超时 300 s、单请求 120 s |
| `backend/providers/openai_compat.py` | 透传 `max_tokens`；解析 `reasoning_content`；兼容 arguments 为对象或字符串 |
| `backend/providers/base.py` | `AssistantTurn` 增加 `reasoning_content` |
| `backend/providers/__init__.py` | 增加 `zhipu` / `glm` provider 模式；缺少 key 时的报错与回退说明更明确 |
| `backend/agent/loop.py` | `llm_step` 轨迹记录 `reasoning_content`（有值时） |
| `backend/cli.py` | `check` 与运行摘要显示 model、base_url、是否真实模型 |
| `repo_service/config.py` | `Settings.from_env()` 前加载本地 env 文件 |
| `.gitignore` | 忽略 `.env`、`.env.local`、`.env.*.local`，保留 `.env.example` |
| `.env.example` | 新增：配置模板，不含真实 key |
| `README.md` | 更新进度表、快速开始、真实模型配置、目录说明 |

## 5. Key 与本地配置

- 真实 key 只写入本机 `.env.local`，该文件已被 `.gitignore` 忽略。
- 仓库内不出现 key；README、交接文档、CLAUDE.md 均不含 key。
- `.env.example` 只含占位符，可安全入库。
- 真实环境变量优先级高于 `.env.local`，便于 CI/服务器覆盖。

## 6. 验证记录

### 6.1 Provider 直连

用 `create_provider(AgentSettings.from_env())` 调用真实模型，发送带 `list_files`
工具定义的请求，返回：

- `is_stub=False`、`model=glm-5.3-flash`、`base_url=https://open.bigmodel.cn/api/paas/v4`
- `tool_calls=[('list_files', {'dir': '/tmp'})]`

说明 function calling 与工具参数解析正常。

### 6.2 完整 Agent 闭环

用临时合成快照（`src/copy.c` 内 `copy_name` 使用 `strcpy` 且丢弃 `dst_len`）
运行 `run_agent`，真实模型自行完成：

1. `grep_content` 搜索函数名；
2. `find_files` 查找 `*.c`；
3. `read_file` 读取实现与调用点；
4. 输出 JSON 结论。

结果：

- `verdict=vulnerable`、`prediction=1`
- `steps=3`、`timed_out=False`、`hit_max_steps=False`
- 证据 4 条，路径均可在快照中复核，证据门控未降级
- 理由明确指出 `strcpy` 无界拷贝与 16 字节栈缓冲区

这是合成冒烟样例，不代表课程测试集上的检测效果。

### 6.3 CLI 编排

用临时数据集（idx 999001/999002）与临时快照执行：

```powershell
python -m backend.cli run --idx 999001 --single --dataset <临时 test.jsonl> --out <临时 run 目录>
```

产物验证：

- `result.jsonl` 仅含 `{"idx": 999001, "prediction": 1}`；
- `report.json` 的 provider 为真实模型（`is_stub=False`, `model=glm-5.3-flash`），`stub_notice=null`；
- `trace.jsonl` 事件类型为 `run_start` / `llm_step` / `repo_tool` / `decision` / `run_end`；
- 结论 `vulnerable`，3 步、2 条可复核证据，证据门控未触发。

### 6.4 数据自检

`python -m backend.cli check` 当前输出（本机未放课程数据）：

```text
[缺失] 测试集: cache/dataset/test.jsonl
[缺失] 样本快照元数据: cache/meta/samples/*.json
[缺失] 快照目录: cache/snapshots（为空或不存在）
[OK] provider: openai-compatible（is_stub=False, model=glm-5.3-flash,
     base_url=https://open.bigmodel.cn/api/paas/v4）
     真实模型已接入（key 来自环境变量或 .env.local）。
结论: 缺少 3 类数据，服务可启动但运行会返回 503。
```

### 6.5 真实数据上的适配

首次直接用真实数据跑时，GLM-5.3-Flash 的长推理把 4096 输出额度耗尽，出现
“content 为空 / 请求超时 / 达到最大步数仍未收尾”。原因和修正：

- **强制 thinking**：GLM-5.3-Flash 不支持关闭 thinking，只能通过 `reasoning_effort` 控制强度。
- **交错思考**：工具调用后必须把上一轮的 `reasoning_content` 原样回传；循环已补上。
- **流式响应**：provider 改为 SSE 流式，避免非流式长推理触发读超时；GLM 同时开启 `tool_stream`。
- **额度与超时**：`max_tokens` 提升到 16384，单样本总超时 900 s，单请求 180 s，重试 2 次。
- **强制收尾**：最后一步禁用工具并提示模型立即输出 JSON；提示词明确禁止依赖 CVE 记忆、`search_knowledge` 最多一次。
- 修正后在 ImageMagick6 样本上，首条漏洞样本 3 步、约 20 s 即给出带 3 条可复核证据的 `vulnerable` 结论。

## 7. 限制与下一步

- 全量评测结果见 [P7 交接文档](2026-11-24-p7-full-eval.md)；P6 只覆盖接入与冒烟。
- 真实模型调用会产生费用；默认上限为 10 步 / 600 s / 单请求 180 s / 16384 输出 tokens，全量驱动可覆盖为 900 s。
- 推理模型可能先产生 `reasoning_content`，再输出最终 JSON；轨迹里同时保留两者，便于排查。
- 若后续要接入 Managed Agents，需要单独设计 session/SSE 执行链路与自定义工具回调，
  不要在现有 `Provider.complete()` 上强行复用。

## 8. 回滚

- 临时回退 stub：设置 `FUNCGUARD_LLM_PROVIDER=stub`。
- 彻底移除真实模型：删除 `.env.local`（或移除其中的 key），provider 工厂会自动回退。
- 本次改动未提交；如需回滚代码，按 `git diff` 恢复上述文件即可。