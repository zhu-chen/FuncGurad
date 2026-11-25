# P8 课程图形输入：手工样本与上传 jsonl（任务 7）

- 日期：2026-11-25
- 分支：`feat/p8-manual-input`
- 认领人：lukezhen
- 关联：任务 7（[.agents/TODO.md](../../.agents/TODO.md)）、[课程要求与实现对照](2026-11-24-course-requirements.md) 第 3–7 页
- 状态：代码与离线测试完成；真实模型 + GitHub 网络的现场演示仍需在配好 key 的机器上补一次

## 1. 范围

课程第 3–7 页要求图形界面既能「输入函数代码与修复提交链接」，也能「上传 `.jsonl` 批量检测」。此前界面只能按测试集 `idx` 启动单条/批量，两个入口都缺。本里程碑只补这两条输入通路：

- 不读测试集也能运行手工样本；
- 上传的 `.jsonl` 逐条独立准备快照与推理；
- 两条通路都不参与全量评测，不写全局 `result.jsonl`。

**没有改**按数据集评分的 `run_service` 路径，**没有改**提示词里的工具调用顺序（后者会使已提交的 300 条评测结果与 HEAD 代码不一致，需要单独立项并重跑，见第 7 节）。

## 2. 改动

| 文件 | 变更 |
|---|---|
| `backend/services/adhoc_service.py` | 新增：手工/上传样本的输入校验、`.jsonl` 解析、快照准备、Agent 运行与报告写出 |
| `backend/api/job_service.py` | 任务记录区分 `dataset` / `manual` / `upload`；新增 `create_manual_run`、`create_upload_batch`；运行阶段写入 `status` |
| `backend/api/app.py` | 新增 `POST /api/runs/manual`、`POST /api/batches/upload` 与 `AdhocError` 处理器 |
| `web/src/api.js` | 新增 `manualRun`、`uploadBatch` |
| `web/src/App.vue` | 输入区新增「手工样本」「上传 .jsonl」；状态文案区分「准备快照」 |
| `web/src/style.css` | `preparing_snapshot` 徽标与手工表单布局 |
| `web/dist/` | 重新构建的静态产物 |
| `tests/test_adhoc.py` | 新增 18 条离线用例 |
| `tests/test_api.py` | 新增 4 条接口校验用例 |

## 3. 接口

### 3.1 手工单样本

```http
POST /api/runs/manual
Content-Type: application/json

{
  "func": "int demo(char *dst, int len) { ... }",
  "commit_url": "https://github.com/owner/repo/commit/<sha>",
  "version": "vulnerable",
  "idx": 194989
}
```

- `version`：`vulnerable`（修复前，取提交的第一父提交）或 `fixed`（修复后，取提交本身）。**由用户显式选择**，系统不替用户猜版本；模型始终只收到一个版本的快照。
- `idx`：可选，仅用于报告展示；不填记为 `0`，报告里 `provided_idx=false`。`result.jsonl` 契约要求整数 `>= 0`，因此不能使用负数或 null。
- 返回 `202` 与 run 记录；`GET /api/runs/{run_id}` 查状态，`/trace` 增量取轨迹，`/report` 取报告。

### 3.2 上传 jsonl

```http
POST /api/batches/upload
Content-Type: application/json

{"content": "{\"idx\":1,\"target\":1,\"commit_url\":\"...\",\"func\":\"...\"}\n..."}
```

- 浏览器读取文件文本后作为 JSON 提交，服务端逐行校验；不引入 `python-multipart` 依赖。
- 字段按要求为 `idx` / `target` / `commit_url` / `func`，`idx` 必须唯一；单次上限 200 条，单条函数文本上限 200,000 字符。
- 错误信息带行号，例如「第 2 行 target 必须是 0 或 1」。
- 与按 `idx` 的批量不同，**不要求按对出现**，也不计算 P-x；P-x 仍由全量评测链路产出。

## 4. 运行阶段

临时样本要先准备仓库快照，可能耗时数分钟。任务状态依次为：

```
queued → preparing_snapshot → running → done / failed
```

前端把 `preparing_snapshot` 显示为「准备快照」，避免用户以为卡死。单条失败只标记该条 `failed` 并给出可读错误，不影响批量其他条目。

## 5. 与课程约束的一致性

- **单版本上下文**：手工输入由用户选版本；上传按每行 `target` 决定。Agent 提示词里仍然只有该函数、该提交链接与该版本快照，没有另一版函数、diff 或变更信息。
- **只读与门控**：复用 `SnapshotGuard` 与 `apply_evidence_gate`，没有放宽任何限制。
- **不联网搜索**：仍然只有四个只读工具，没有新增网络能力。
- **契约**：临时样本写自己的 `result.jsonl`（仅 `idx`/`prediction`）、`report.json`、`trace.jsonl`，不改变 `shared/contracts/`。

## 6. 验证

以下命令均在仓库根目录执行。

```bash
python -m pytest -q
```

实测：`112 passed, 1 skipped, 1 xfailed`（新增 18 + 4 条）。

手工离线验证（不需要 key/网络）由 `tests/test_adhoc.py` 覆盖：假快照 + stub provider，检查阶段回调、报告字段、`result.jsonl` 两字段、`trace.jsonl` 事件。

接口冒烟（本机无测试集、stub provider）：

```bash
FUNCGUARD_LLM_PROVIDER=stub python -m backend.server --port 8123
```

| 请求 | 结果 |
|---|---|
| `GET /api/health` | 200，`status=degraded`（缺测试集），不影响手工入口 |
| `POST /api/runs/manual`（`ftp://` 链接） | 400 `adhoc_error`：链接必须是 http(s) |
| `POST /api/batches/upload`（非 JSON） | 400 `adhoc_error`：第 1 行 JSON 格式错误 |
| `POST /api/runs/manual`（合法但仓库不存在） | 202；随后 run 变 `failed`，错误信息可读 |
| `GET /` | 200（新前端已托管） |

真实公共仓库 + stub 的端到端冒烟（2026-11-25 本机，`octocat/Hello-World` @ `7fd1a60`）：

| 场景 | 结果 |
|---|---|
| 手工单样本（`version=fixed`） | `queued → preparing_snapshot → done`；报告 `mode=manual`、`runs=1`、`failures=0`；`trace.jsonl` 8 个事件；`result.jsonl` 仅 `idx`/`prediction` |
| 上传两条（同仓库，`target=0` 与 `target=1`） | batch `done`、2/2 成功；`target=1` 正确解析第一父提交；逐条报告独立 |

> 尚未完成：真实模型 + 浏览器界面的现场演示（本机没有 `.env.local`）。演示前请按 README 配置 key，并确认 GitHub 限流余量。

## 7. 未覆盖与后续

1. **检索顺序**：提示词仍是「先定位函数、再读仓库」，`search_knowledge` 是可选工具，与课程「先检索、再初判、证据不足才读仓」不一致。改动会改变检测行为，需单独立项；若重跑 300 条，预计仍需数小时与模型费用，结论要同步更新 README 与 P7 交接。
2. **版本身份**：prompt 同时给出 `commit_url` 与带提交号的快照路径，存在间接暴露版本身份的可能，建议与提示词改造一起评估。
3. **手工样本没有 P-x**：单条手工运行只给判定与理由；只有按 `idx` 的配对批量才有 P-x。
4. **idx 缺省值**：未提供 idx 时统一记为 0，仅作报告展示；多份手工报告之间不要用 idx 互相区分。
5. **前端未做端到端浏览器测试**：本次只保证构建通过、静态资源可托管、接口契约在线；建议验收前在浏览器里各点一次两条新流程。
