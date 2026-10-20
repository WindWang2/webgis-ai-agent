# H02 PERF — 客观性能报告

- 环境：worktree venv Python 3.12.x，本机（非 CI）；median-of-N。
- 测试：`tests/benchmarks/test_h02_cartography_perf.py`（`-m perf` 车道，#664 隔离策略）。
- 结果：`pytest tests/benchmarks/test_h02_cartography_perf.py -m perf --no-cov -q` → **6 passed in 9.26s**（exit 0）。

## 实测（2026-10-20）

| 工作负载 | 中位数 | 预算上限 | 断言 |
|---|---|---|---|
| build_product_manifest 100 层 | 3.46 ms | 250 ms | 图层投影 128 封顶 |
| build_product_manifest 1000 层 | 26.67 ms | 2000 ms | 同上（有界投影） |
| verify_export_semantic_consistency 100 层 | 3.42 ms | 600 ms | 状态 ∈ {pass, warning} |
| verify_export_semantic_consistency 1000 层 | 26.84 ms | 4000 ms | 同上 |
| 反复导出 manifest 稳定性（100 次） | <1 ms/次 | 500 ms | 100 次逐字节一致 |
| required 槽推导 ×290（29 模板×10） | <0.1 ms/次 | 5 ms | 注册表 O(1) 形状 |

- 峰值 RSS（进程级，含导入）：48 MB。
- 说明：这些是**结构性预算**（宽上限钉算法形状：有界投影/注册表查找/
  确定性），不是速度基准；预算按 CI 噪声裕度放宽，不宣称"提升 XX%"。

## 相关既有 lane（引用，非本次新增）

- `test_template_registry_perf.py` / `test_perf_mapspec_svg_compile.py`
  等既有 perf 基准未改动。
