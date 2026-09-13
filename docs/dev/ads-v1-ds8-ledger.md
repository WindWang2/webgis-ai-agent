# ads-v1 · DS8 交付台账（任务 → 文件 → 测试 → 证据）

> 波次：DS8 · 可观测、成本治理与验证矩阵 · ADR-0178 · 里程碑 M5（与 DS9 同车）
> 状态：**完成** · 2026-09-13

| 任务 | 文件 | 测试 | 证据 |
|---|---|---|---|
| D4 全字段落库 | 迁移 `0071_ads_acquisition_facts`（facts + budgets 两表，outcome CHECK 词表）+ `facts.py::FactsStore` | `tests/unit/test_data_fabric_facts.py`（14 测） | 全字段落库（含 fallback_json/drift/wave/ts）；**telemetry 覆盖率 100%**（success/degraded/failed 全记录闸测试）；`alembic heads` 单 head（0071←0070） |
| 验证矩阵 | `app/services/data_fabric/matrix.py`（12 源 × 12 请求型 × 3 场景 × 2 语言）+ 产物 `docs/dev/ads-v1-validation-matrix.csv` | `tests/data/test_ads8_matrix.py`（3 闸：864 维度/核心 216/CSV 同步） | **核心 216/216，全量 864/864 全过**（离线确定性）；英文维度暴露并修复 last-five-years 解析缺口 |
| Ratchet 门禁 | `facts.py::ratchet_check`（源×指标×波次 p50；容差单点 TOLERANCE；**零基线→任何增长即劣化**） | `test_ratchet_intercepts_injected_latency_spike` / `..._retry_storm_and_forced_degradation` / `..._passes_stable_waves` / `..._new_source_not_flagged` | **注入劣化 100% 拦截**（时延尖峰/重试风暴/强制降级）；稳定与新增源不误报 |
| 成本看板 | `scripts/ads_cost_dashboard.py` → `docs/dev/ads-v1-cost-dashboard.md` | 预算告警闸：`test_budget_alert_on_overrun` / `test_budget_no_alert_within_limits` | 12×12 预算行（provisional）；`ads_*` 表隔离（仅展示层共享）；check_budget 四指标告警 |
| 排序模型校准 | `scripts/ads_calibrate.py` → `docs/dev/ads-v1-calibration.md`；`ranker.WEIGHTS` 更新（rel .55→.65，cost 保 0.05 下限拒绝 sweep 的 0） | DS2/DS6/DS7 检索相关 39 测复验全绿 | MRR 0.9194→0.9203；**provisional → 定稿**（依据记录于校准报告） |
| 阈值校准 | 代价模型常数（bytes/row 128B vs 实测 157.6B，偏差 18.5%） | DS3 偏差闸（P50 ≤30%） | 维持；DS0 表面常量未触发校准条件，维持 |

## 波次验收对照（§6 DS8 行）

- [x] 核心 216 组跑通入库（864/864 全量亦过；CSV 逐组台账入库）
- [x] ratchet 注入劣化 100% 拦截（4 组 ratchet 闸测试）
- [x] 埋点覆盖 100%（三 outcome 全记录闸测试）
- [x] 成本预算告警生效（超限/限内两向闸测试）
- [x] 排序权重与阈值完成校准并转定稿（校准报告 + WEIGHTS 单点更新）

## 备注

- 迁移 0071（本线 70–79 段第二个）；矩阵为规划/链/门禁层的确定性验证——真实流量埋点随 DS9 运行面接入。
- 校准纪律：sweep 结果**不盲采**（cost=0 拒绝），本地优先语义优先于 0.09% 的 MRR 提升。
