# V6 测试矩阵与本地验证结果（Phase D）

## 新增测试（本 Epic）

| 层 | 文件 | 用例 | 覆盖 |
|---|---|---|---|
| IR | test_federated_logical_plan.py | 12 | 树构建/canonical hash 确定性/序列化往返/全部节点形状 |
| 统计 | test_federated_spatial_stats.py | 15 | 直方图确定性/选择率边界/复杂度/收割 |
| 成本 | test_federated_costing.py | 17 | 空间选择性 basis/CRS 决策（server opt-in/本地最小成本侧） |
| 枚举 | test_federated_enumerator.py | 10 | DP 确定性/bushy/剪枝界限/CRS 决策入树/位置回退 |
| Bloom | test_federated_bloom.py | 12 | 无假阴性/确定性/饱和降级/盈利阈值 |
| 物理 | test_federated_physical_executor.py | 10 | **V6≡V5 差分**（属性/空间/聚合/三跳）/分页/取消/超时/CRS 变换 |
| 下推 | test_federated_pushdown.py | 6 | fetch 窗口 V5 奇偶/聚合下推裁决/边界解释 |
| 自适应 | test_federated_adaptive.py | 10 | 观测/受护栏重排/方向边/执行器集成 |
| Explain | test_federated_explain.py | 5 | 确定性/est vs actual/CRS 披露 |
| 接线 | test_federated_engine_wiring.py | 6 | engine 分派/回退/typed 契约/同源委托/V5 位级不变 |
| 差分 | test_federated_v6_differential.py | 56 | 10 种子语料 + 聚合 7 种 + 边界（NULL/空串/数值键/bbox/空集） |
| 基准 | test_federated_v6_benchmark.py | 6 | 请求缩放/Bloom 盈利/过滤收益/硬界/请求不放大/估计质量 |
| 契约 | test_data_fabric_optimizer_v5.py 增补 | +1 | V5 链契约（engine=v5）+ V6 树形对照 |

合计新增 ≈ 166 用例。

## 本地验证结果（全部真实执行）

- V6 目标套件（federated/* + v3/v5/v4 federation/stats）：**288 passed**
- CI 契约层（tool meta / context isolation / ci-local / perf coverage）：**27 passed**
- 广域切片（-k "data_fabric or federated or adapter_contract or tool"）：**3103 passed, 7 skipped**（104s）
- **完整 tests/unit：8514 passed, 105 skipped**（9m33s，单进程 --timeout=120）
- `ruff check app tests`：**All checks passed**
- 前端未触碰（无 frontend 变更；eslint/vitest/build 不适用）
- perf/cartography/real_services lanes：未触碰对应面（query path 无既有 perf 门；
  新增基准为结构断言，非 wall-clock 门）

## 性能记录（结构性，参考值非门）

- 两源 600 行 = 每源 1 页（页 2000）→ 请求有界；
- 基数估计（NDV 模型）实际偏差 ≤16×（测试上限，实测同数量级）；
- Bloom 位图 ≤128Kb；盈利阈值拒绝 1M 键场景（构建 32MB > 节省）；
- 自适应 replan ≤1，请求次数不放大（≤8 次断言）。
