# 单元测试基线（2026-11-24）

- 分支：`test/baseline`，从 `main`（`9884cd4`）拉出。
- 合并状态：已合并（2026-11-24）进 `main`；如需追溯，用 `git log -- docs/handoff/2026-11-24-tests-baseline.md` 查看。
- 原则：只新增测试与测试配置，不改任何生产实现；`backend/agent/`、`scripts/`、`shared/contracts`、`cache/` 均未改动。

## 测了什么

新增 `tests/`，共 7 个测试文件、90 条通过用例（另有 1 条默认跳过、1 条已知缺陷 xfail）。

| 范围 | 文件 | 覆盖点 |
|---|---|---|
| 评分口径 | `tests/test_bench_score.py` | `P-C/P-V/P-B/P-R` 四类计数；A/B/C/D 阈值（30/25/20）；`P-R*2 > P-C` 降级；全对、全反、全 benign、全 vulnerable、混合、缺失整对跳过、重复 idx 冲突/一致、未知 idx；字段类型与取值校验 |
| 数据集 | `tests/test_dataset.py` | 按 `target` 而非顺序识别漏洞版/修复版；奇数条、同 target 对、重复冲突 idx、空文件、坏 JSON 行号定位；字段严格校验；真实 300 条/150 对用例默认跳过 |
| 路径守卫 | `tests/test_snapshot_guard.py` | `../`、反斜杠越界、绝对路径、空路径、符号链接逃逸全部拒绝；快照根内放行；二进制与超大文件拒绝；`find_files`/`grep_content`/`read_file` 的越界拒绝行为 |
| Agent 循环 | `tests/test_agent_loop.py` | 无成功工具调用却报 vulnerable → 降级 benign；有工具但 evidence 为空/不可验证 → 降级；有成功工具 + 可验证 evidence → 保留 vulnerable；最大步数与总超时都能收敛；provider 异常与非法 JSON 保守判 benign。全部用假 provider，不联网 |
| 契约与产物 | `tests/test_contract_artifacts.py` | `result.jsonl` 每行严格只有 `idx`/`prediction`，用 `shared/contracts/result_record.schema.json` 校验；多余字段、缺字段、类型错误、非法值均拒绝；运行产物目录同时生成 `result.jsonl`/`report.json`/`trace.jsonl`；trace 事件符合契约且同一 run 内 `seq` 连续 |
| RAG | `tests/test_rag_retriever.py` | BM25 索引加载、top-k 与上限 20、项目/CWE 过滤、命中 9 字段、空查询与索引缺失的可读错误、卡片数与索引不一致时报错、`get_retriever` 缓存与 `reset_retriever` |
| API | `tests/test_api.py` | 缺数据时 `/api/health` 返回 200 + `degraded`；`POST /api/runs` 返回 503 `data_not_ready`；`/api/knowledge/search` 返回 503 `knowledge_not_ready`；有数据时 health/pairs 结构正确；参数校验 422 与未知 idx 400 |

评分口径依据：仓库内没有课程官方 `evaluation.py`，阈值与降级规则按 `bench/score.py` 实现、`EvalReport` 契约和 P1 交接文档（自研脚本与官方脚本四类计数一致）推导，已写进测试文件顶部注释。若后续把官方脚本放进仓库，需要重新复核阈值。

## 怎么跑

```powershell
python -m pip install -r requirements-dev.txt
pytest -q
```

`pytest.ini` 把收集范围限定为 `tests/`。原因是仓库根 `cache/` 下有约 12 万个运行产物文件，未限定范围时 pytest 会递归扫描根目录，启动非常慢。

本机实测输出：

```text
......x................................................................. [ 78%]
............s.......                                                     [100%]
90 passed, 1 skipped, 1 xfailed in 7.27s
```

- `s` = `test_real_dataset_shape`，需要 `cache/dataset/test.jsonl`，干净 clone 默认跳过。
- `x` = `test_hit_max_steps_flag_reports_true_on_exhaustion`，已知生产缺陷，见下节。

## 已知失败项

`backend/agent/loop.py` 只在“`final` 为空且不是超时、不是非法回答”的分支里设置 `hit_max_steps`，但该分支实际不可达：步数耗尽时要么读到非法 JSON，要么已经超时。因此 `AgentOutcome.hit_max_steps` 在真实运行中恒为 `False`，报告和前端拿不到“是否撞到步数上限”的可靠信号。

- 测试：`tests/test_agent_loop.py::test_hit_max_steps_flag_reports_true_on_exhaustion`
- 处理：按“不改生产代码”的约束，用 `xfail(strict=True)` 记录；修好后该用例会转红，提醒更新预期。
- 影响：仅影响观测字段，不影响“步数耗尽会终止并判 benign”这一主行为（已由另一条用例覆盖）。

## 没测什么

- 未跑真实模型、未跑 300 条评测、未读取 `cache/` 内任何真实数据。
- 未覆盖 `repo_service/` 的 git 快照与函数定位（需要真实仓库与网络）。
- 未覆盖 `scripts/` 下的全量评测驱动。
- 未覆盖前端 `web/`（Vue 组件、错误提示与构建产物）。
- 未覆盖 CLI（`backend.cli`）的端到端行为。
- 未覆盖 SSE/轮询式轨迹推送的时序细节，只验证了 trace 文件契约。
- API 只测单条运行入口的错误路径，没有真正启动后台线程跑完整 run。

## 后续建议

1. 修复 `hit_max_steps` 的赋值位置（放在循环结束后按 `steps >= max_steps` 计算），然后把 xfail 改为普通断言。
2. 给 `repo_service` 的 URL 解析与快照目录解析补离线单测（用本地 git 仓库夹具，不联网）。
3. 补 CLI `check` 的退出码与提示文案测试，避免首次运行回归。
4. 若要覆盖前端，优先给 axios 拦截器的 401/409/503 分支补 mock 测试。
5. 官方 `evaluation.py` 入仓后，用 `bench/fixtures/` 固定样本做一次交叉校验，并把阈值来源写进 `tests/test_bench_score.py` 顶部。
