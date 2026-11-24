# tests

安装测试依赖后运行（在仓库根目录）：

```powershell
python -m pip install -r requirements-dev.txt
pytest -q
```

`pytest.ini` 已把收集范围限定在 `tests/`，避免扫描 `cache/` 下的上千个运行产物。

## 覆盖范围

| 文件 | 守的底线 |
|---|---|
| `test_bench_score.py` | P-C/P-V/P-B/P-R 计数、A/B/C/D 阈值、`P-R > P-C / 2` 降级 |
| `test_dataset.py` | 300 条 / 150 对的读取校验、按 target 配对、坏数据可读报错 |
| `test_snapshot_guard.py` | 越界路径（`../`、绝对路径、符号链接）必须拒绝，快照根内放行 |
| `test_agent_loop.py` | 无证据的 vulnerable 降级为 benign、最大步数与总超时保护 |
| `test_contract_artifacts.py` | `result.jsonl` 只有 `idx`/`prediction`，三个运行产物齐全、trace 合规 |
| `test_rag_retriever.py` | 索引加载、top-k、项目/CWE 过滤、缺索引与空查询的可读错误 |
| `test_api.py` | 缺数据时 `/api/health` 200+degraded、写接口 503、有数据时结构稳定 |

## 数据依赖

- 默认用例全部使用 `tmp_path` 自造夹具，不需要课程数据集、快照、知识索引、网络或模型 key。
- 依赖真实数据的用例用 `skipif` 标记，干净 clone 上默认跳过：
  - `tests/test_dataset.py::test_real_dataset_shape`（需要 `cache/dataset/test.jsonl`）
- 已知生产缺陷用 `xfail(strict=True)` 记录，仓库修复后该用例会立即转红提醒更新：
  - `tests/test_agent_loop.py::test_hit_max_steps_flag_reports_true_on_exhaustion`
    （`backend/agent/loop.py` 的 `hit_max_steps` 分支实际不可达，步数耗尽时该标记恒为 False）

测试只读调用生产代码，不修改 `shared/contracts`、`backend/agent`、`scripts/` 或 `cache/`。
