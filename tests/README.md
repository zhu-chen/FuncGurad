# tests

按 [README 的 Python 环境说明](../README.md#python-环境)用 uv 或 pip 安装运行依赖和测试依赖，然后在仓库根目录的已激活环境中运行：

```powershell
python -m pytest -q
```

`pytest.ini` 已把收集范围限定在 `tests/`，避免扫描 `cache/` 下的上千个运行产物。

## 覆盖范围

| 文件 | 守的底线 |
|---|---|
| `test_bench_score.py` | P-C/P-V/P-B/P-R 计数、A/B/C/D 阈值、`P-R > P-C / 2` 降级 |
| `test_dataset.py` | 300 条 / 150 对的读取校验、按 target 配对、坏数据可读报错 |
| `test_snapshot_guard.py` | 越界路径（`../`、绝对路径、符号链接）必须拒绝，快照根内放行 |
| `test_agent_loop.py` | 无证据的 vulnerable 降级为 benign、最大步数与总超时保护 |
| `test_agent_prompts.py` | 默认提示词配置与非法值校验、原系统和用户提示词逐字校验 |
| `test_contract_artifacts.py` | `result.jsonl` 只有 `idx`/`prediction`，报告和轨迹记录实际调用顺序，旧报告兼容 |
| `test_rag_retriever.py` | 索引加载、top-k、项目/CWE 过滤、缺索引与空查询的可读错误 |
| `test_api.py` | 缺数据时 `/api/health` 200+degraded、写接口 503、有数据时结构稳定 |

## 课程验收

课程验收还需通过图形界面实际提交一个函数及其修复提交链接，再上传一份批量 `.jsonl`，核对运行中的检索与读仓轨迹以及最终报告。现有离线用例覆盖评分、工具边界、产物契约及脚本化模型的检索顺序。图形输入、指定提交的选择和真实模型的流程及全量结果需单独验证。见[课程要求与实现对照](../docs/handoff/2026-11-24-course-requirements.md)。

## 数据依赖

- 默认用例全部使用 `tmp_path` 自造夹具，不需要课程数据集、快照、知识索引、网络或模型 key。
- 依赖真实数据的用例用 `skipif` 标记，干净 clone 上默认跳过：
  - `tests/test_dataset.py::test_real_dataset_shape`（需要 `cache/dataset/test.jsonl`）
- 2026.11.26 离线基线为 103 passed / 1 skipped / 0 xfailed。`hit_max_steps` 回归用例覆盖触发最后一步强制收束与提前结束的标记。

测试只读调用生产代码，不修改 `shared/contracts`、`backend/agent`、`scripts/` 或 `cache/`。
