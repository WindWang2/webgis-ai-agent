# G02-GIS-EXECUTION-COMPILER TEST MATRIX

## 本方向新增测试

| 文件 | 数量 | 覆盖（Oracle） |
|---|---|---|
| `tests/unit/gis_harness/test_execution_compiler_characterization.py` | 19 | C1 IR→族/表达/义务编译（确定性/信号溯源/user-vs-recommended/blocked/needs_clarification）；C2 验收桥（行投影/节点成功≠目标/failed 诚实）；C3 conformance（live/悬空/指纹）；C4 失效面（style 零失效/换城全量/统计最小集）；C5 单位（计数≠率/每万人披露/时间距离互斥/密度义务） |
| `tests/unit/gis_harness/test_execution_compiler_oracles.py` | 30 | O8 语料 runner 23 类 + 重复确定性 + 报告产物；O1 零悬空信号；O2 fail-closed（allowlist 空/词表封闭/悬空工具/live 全 pass）；O5 秒≠米/#2006 形态/密度门槛数据驱动；O6/O7 最小重算/换城全量；O4 生产桥可达/幂等/披露；M7 对抗（空文档/巨型输入/控制字符/并发纯度/损坏态退化） |

## 命令

```bash
.venv/bin/python -m pytest tests/unit/gis_harness/test_execution_compiler_characterization.py \
  tests/unit/gis_harness/test_execution_compiler_oracles.py -q --no-cov
# → 49 passed

.venv/bin/python -m python -m app.services.gis_harness.execution_compiler.corpus \
  reports/execution_compiler_benchmark.md   # planner 评估报告（23 用例逐条解释）

.venv/bin/python -m ruff check app/services/gis_harness/execution_compiler/   # 0 error
.venv/bin/python -m mypy app/services/gis_harness/execution_compiler/          # 0 error
```

## 回归面（改动波及的既有套件，全部本地验证）

| 套件 | 结果 |
|---|---|
| `tests/unit/gis_harness/`（-k "requirement or intent or patch or digest or execution_compiler"） | 469 passed, 1 skipped |
| `tests/unit/gis_harness/analysis_workflows/` | 190 passed（含本方向 characterization） |
| mypy 三改动文件（build/pipeline/analysis_workflow_tools） | 42 errors = master 基线（零新增） |

## 未验证边界（诚实披露）
- 线上 CI 不等待不宣称（任务书红线）；全部为本地验证。
- `webgis_requirement_compile` 工具的端到端 Pi 联调未做（工具注册面
  已验证：init_tools 全量注册 `r.has` 为 True；会话文档加载逻辑与
  tool_map_intent 同源模式）。
- acceptance bridge 的 `load_workflow_runs` 对 Redis 降级路径按诚实空
  处理（单测覆盖损坏态；真实 Redis 故障注入未做——fail-open 语义即
  目标行为：评估回退原 chapter，零漂移）。
