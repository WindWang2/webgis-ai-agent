# AC-10 执行计划与交付台账（adaptive-cartography/10-quality-baseline）

> ADR-0159 配套。任务 → 文件 → 测试 → 证据 逐项对照；门禁证据全文见 PR 描述
> 与 `quality_gate_local.sh` 输出。

## 交付台账

| 阶段 | 交付物 | 文件 | 测试 | 证据 |
|---|---|---|---|---|
| §0.1 | 独立 worktree + venv | `../webgis-wt-ac-10`（branch `adaptive-cartography/10-quality-baseline` @ 1fd4b035） | — | 10900 单测可收集 |
| §0.2 | 防重复复核纪要 | `docs/dev/ac-10-decisions.md` D8 | — | PR/issue/branch/grep 四路检索，无同构实现 |
| §0.3 | ADR-0159 + 迁移领号 | `docs/adr/0159-*.md`；`migrations/.alloc.json`（本线段 0056–0065） | `test_alembic_metadata.py`（单头/唯一/alloc） | 0056_cartography_quality_facts（down=184068cb4249） |
| P0 | 度量资产清单 | `docs/dev/ac-10-metrics-inventory.csv`（19 资产） | — | S1 产出，CSV 9 列对齐 |
| P0 | 6 规则 × 17 场景首轮基线 | `ac-10-rule-distribution.csv`（102 行）/ `ac-10-rule-baseline-first.json` | — | 42 条有限观测全 pass；label.collision_est 在该场景集零观测（诚实缺席） |
| P0 | 9 场景时长与稳定性 | `measure_scenario_timing.py` + PR 附 JSON | — | 27 次全绿、全部 <14s（预算 <30s）、零 flaky |
| P0 | cartography 覆盖率现状 | PR 附输出 | — | cartography 50.10% / harness 46.33%（scope 分开计） |
| P1 | 质量事实库 | `migrations/versions/0056_*.py`、`app/models/cartography_quality.py`、`app/services/cartography_metrics_store.py`、钩子 `cartography_runtime.py` / `harness_runner.py`、`app/core/config.py` 开关 | `tests/quality/test_cartography_quality_facts.py`（10） | 写入→最近 30 次检索 ✓；保留 90d/5000 run 双路径；停用/故障不反噬 |
| P2 | ratchet 闸 | `app/services/cartography_ratchet.py`、`scripts/quality_ratchet_gate.py` | `test_cartography_ratchet.py`（11） | 注入劣化 2/2 拦截；waiver 到期失效；provisional 不拦截 |
| P3 | golden 图像与晋升 | `app/lib/cartography/golden_diff.py`、`scripts/golden_baseline.py`、9×`tests/fixtures/runtime/*/golden/` | `test_golden_image_diff.py`（11） | 9 场景 verify within_ratio=1.0 / max_diff=0；7 pr-blocking / 2 nightly-only / 0 quarantine |
| P4 | 覆盖率闸 | `scripts/coverage_cartography_gate.py` + `quality_gate_local.sh` 步骤 1 | （scope 聚合函数烟测） | floor=50 判定 ✅（50.10%）；与后端 75% 分开计 |
| P5 | 自适应验收集 | `tests/quality/test_adaptive_acceptance_suite.py` | 2 用例 | 3 轮不劣化 + 收敛(≤0.25) + 无 repair_exhausted + adaptive lane 落账 |
| P6 | 校准入库 | `scripts/calibrate_cartography_thresholds.py`（--write，dry-run 默认） | `test_calibrate_write.py`（3） | provisional 入库 + diff old→new；硬编码默认值快照不变 |
| P7 | 趋势报告 + 看板 | `scripts/quality_trend_report.py`、`docs/dev/ac-10-quality-dashboard.md` | — | 文本表 + CSV + 数值段自动填充；六项顶层指标（缺数据=未测量） |
| P8 | 本地一键门禁 | `scripts/quality_gate_local.sh` | — | 全量四步一次跑通，完整输出附 PR |
| P8 | 文档 | ADR-0159、`ac-10-decisions.md`、`ac-10-baseline-recon.md`、本文件、CHANGELOG | — | — |

## 契约说明（其他线接入）

- 质量指标入账 API：`app/services/cartography_metrics_store.record_quality_run`
  （lane 词表 `desired_state/runtime/eval/adaptive/golden`；summary 有界投影）。
- 自适应验收口径：`test_adaptive_acceptance_suite.py` 的
  `symbology_decision_signature`（03 线 SymbologyDecision 落位缝）、
  `quality_score`、`signature_distance`。
- 09 线 record-only→阻断切换：ratchet 基线激活（`baseline --activate`）即切，
  建议 nightly ~10 绿后执行。

## 已知边界

- 覆盖率下限爬坡 50→60 未完成（D1，ratchet 纪律只升不降）。
- golden 的跨平台稳定性依赖渲染栈版本锁定；MapLibre/字体升级需显式 `generate`
  刷新并附 diff 说明。
- Windows 本地浏览器链路的 `.cmd` shim 只在测量脚本内（D5），CI/Linux 不受影响。
