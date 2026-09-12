# AC-10 制图质量常驻看板

> 本页是任务书 `adaptive-cartography/10-quality-baseline` §4/§5 验收总纲的常驻同源面。
> 数值段由 `scripts/quality_trend_report.py --dashboard` 自动刷新；本段散文之外，
> 标记块之间内容勿手改。门禁复现：`bash scripts/quality_gate_local.sh`。

## 六项顶层指标口径

| 指标 | 口径 | 数据来源 |
|---|---|---|
| 全自动率 | 有判定 run 中 `passed=true` 的占比（人工介入次数暂无独立度量源，诚实口径见左） | 事实库 `cartography_quality_runs` |
| 一次成功率 | desired_state lane 通过且零修复尝试 | 同上 |
| 自愈成功率 | runtime lane 有修复尝试且终态通过 | 同上 |
| 一致性 | golden lane（像素级 golden + 墨量带校验）通过率 | 同上 |
| 出版就绪 | publish/export 产出处入账后计算（08 线接入前为 未测量） | 08 线调用本线写入 API |
| 回归守护 | ratchet active 基线数 / 劣化 / waived | `cartography_quality_baselines` |

诚实纪律：没有数据的指标显示 `未测量`，**绝不显示 0**。

## 场景晋升分档（ADR-0065）

- `pr-blocking`（7 个）：heatmap-basic / interpolate-circle / match-line /
  mvt-basic / raster-overlay / step-fill / symbol-label —— 首轮本地 3×绿 +
  全部 <30s；连续 10 次绿历史由本线事实库随 nightly 累积。
- `nightly-only`（2 个）：fault-missing-source / fault-wrong-color ——
  负路径夹具（expect=fail），由 nightly 全量矩阵拥有。
- `quarantine`：当前无（27 次运行零 flaky）；机制见
  `scripts/golden_baseline.py status --promotion quarantine --note ...`。

<!-- AC10:AUTO:BEGIN（脚本生成段，勿手改） -->

### 数值段（脚本自动生成 @ 2026-09-12 23:33 UTC）

#### 顶层六项指标

| 指标 | 当前值 |
|---|---|
| 全自动率 | 85.7% |
| 一次成功率 | 未测量 |
| 自愈成功率 | 未测量 |
| 一致性 | 100.0% (n=18) |
| 出版就绪 | 未测量 |
| 回归守护 | 未测量（无 active 基线） |

#### 检查项趋势（最近 10 次 run，旧→新）

```
制图质量趋势（最近 10 次 run，按检查项；旧→新）
============================================================================================
check_id                                               n      first       last        min        max lane          trend
gate.CartographicQuality                               1          0          0          0          0 runtime       →
gate.CursorResolutionRate                              2          0          0          0          0 eval          ＝
gate.ErrorRecoveryRate                                 2        100        100        100        100 eval          ＝
gate.MapSpecValidity                                   2        100        100        100        100 eval          ＝
gate.StepEfficiency                                    2        100        100        100        100 eval          ＝
gate.ToolChoiceAccuracy                                2        100        100        100        100 eval          ＝
```

<!-- AC10:AUTO:END -->
