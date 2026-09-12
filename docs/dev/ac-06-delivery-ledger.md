# AC-06 交付台账（任务 → 文件 → 测试 → 证据）

分支 `adaptive-cartography/06-symbol-law-runtime`（基线 origin/master @ 1fd4b035）。

## P0 勘察

| 任务 | 产出 | 证据 |
|---|---|---|
| 符号常量清单 | `docs/dev/ac-06-symbol-constants.csv`（41 行，逐行 disposition） | grep 记录见 recon §1 |
| recompile 触发路径 | `docs/dev/ac-06-runtime-recon.md` §2（三入口 + runtime 消费链） | reconciler.ts 行号索引 |
| 10k 性能基线 | `frontend/lib/mapspec-compiler/mapspec-diff.perf.test.ts` + recon §3 | 基线原文：`PERF\|compile_10k\|12.662`、`diff_paint_only\|0.007`、`diff_one_changed\|6.232`、`diff_rev_equal\|9.681` |
| type × StyleMethod 矩阵 | recon §4（9 层型 × 7 方法，缺口标红） | compiler.ts :313-416 if/else 链 |
| 防重复复核 | recon §9（《复核纪要》） | gh pr/issue/branch 检索 + ADR-0118/0126/0138 约束 |

## P1 符号律引擎

| 任务 | 文件 | 测试 |
|---|---|---|
| f(zoom,count,geometry) 表达式引擎 | `frontend/lib/map-kit/symbol-law.ts`（新） | `symbol-law.test.ts` 24 passed |
| 出厂默认值表（可覆盖） | `DEFAULT_SYMBOL_LAW` | 表达式形状/单调性/clamp/确定性断言 |
| density_signal 唯一份 | `densitySignal` + `density_signal` 别名 | 「exports the snake_case contract alias」 |
| renderer 接线（销项 0.8/6/常量 px） | `renderer.ts` addThematicLayer / addNativeHeatmap | renderer.test.ts 366 passed（旧常量断言改律断言） |

## P2 密度自适应切换

| 任务 | 文件 | 测试 |
|---|---|---|
| 密度裁决（可配阈值） | symbol-law `resolveDensityPresentation` | symbol-law.test.ts |
| compiler 自动聚合/热力改写 + evidence | `compiler.ts` 预扫描 + 源聚合注入 | `compiler.density.test.ts` 5 passed |
| 切换写 evidence | `recordSymbolLawEvidence("density-switch"…)` | density 测试 evidence 断言 |

## P3 属性级增量更新

| 任务 | 文件 | 测试 |
|---|---|---|
| patch kinds + 键级分解 | `reconciler.ts`（`diffLayerKeys`、paint/layout kinds） | reconciler.test.ts / reconciler.filter.test.ts（全绿） |
| setPaint/setLayout + 回落 + 计数 | `runtime.ts`（applyPaintPatchSafe/applyLayoutPatchSafe）、`perf-counters.ts`（6 新计数器） | `runtime-incremental.test.ts` 6 passed |
| 改色零 remove/add | 同上 | 「改色零 remove/add」用例（layerRemoves=0 断言） |
| 回落 evidence | runtime → symbol-law evidence | 「setPaintProperty 被拒 → 回落」用例 |

## P4 表达力补齐

| 任务 | 文件 | 测试 |
|---|---|---|
| schema 胶水 + 再生成 | `app/lib/cartography/mapspec_schema.py`、`ts_projection.py` → `types.generated.ts` | `pytest tests/cartography/test_mapspec_schema_v6.py test_ts_projection_contract.py` 26 passed |
| symbol/background/hillshade/dash/blur/translate 分支 | `compiler.ts` + `paint-bridge.ts`（同构 canonical 表） | `compiler.matrix.test.ts` 71 passed（9×7 全矩阵） |
| interpolate exponential/cubic-bezier/zoom | `ts_projection.py` STYLE_METHOD_BLOCK + `compileStyleMethod` | matrix 用例逐字断言算子 |
| 未知 source 显式报错 | compiler `UNKNOWN_SOURCE_TYPE` + evidence | matrix/`compiler.test.ts` 改写用例 |
| live-spec 层型表 additive | `lib/mapspec/live-spec.ts`（2 行） | typecheck + 全量 vitest 绿 |

## P5 diff 性能

| 任务 | 文件 | 测试/证据 |
|---|---|---|
| content_revision + 指纹短路 | `reconciler.ts`（`sourceDefinitionsEqual`、WeakMap FNV-1a） | perf harness：`diff_one_changed 6.232→0.002ms`、`diff_rev_equal 9.681→0.005ms` |
| compile 不劣化 | — | `compile_10k 12.662→13.786ms`（±10% 内，本机噪声带） |
| syncLayerZOrder 最小移动集 | `renderer.ts`（LDS 保留集 + `noteStyleLayerMovedBelow`） | renderer-m4.test.ts（1 次 move 达成期望栈序 / 已就绪 0 次）+ runtime.test.ts（新挂层 0 次冗余 move） |
| 中间态 typecheck | — | `tsc --noEmit` exit 0（0 错误） |

## P6 legend_spec v2

| 任务 | 文件 | 测试 |
|---|---|---|
| out_of_range 裁剪尾 guard | `thematic-paint.ts`（withOutOfRangeGuard，nodata 外层） | `thematic-paint.v2.test.ts` 6 passed |
| metadata evidence（unit/k/method/…） | `discloseLegendV2Metadata` | v2 测试 evidence 断言 |
| 03 线 schema 本地 fixture | `docs/dev/ac-06-legend-spec-v2.schema.json`（03 分支冻结版快照） | 测试内 schema 关键字段核对 |
| paint-bridge unmapped evidence | `paint-bridge.ts` passthrough 循环 | paint-bridge.test.ts（「无法映射的键写入 evidence」） |

## P7/P8 门禁取证

- `pnpm exec vitest run lib/mapspec lib/map-kit lib/mapspec-compiler lib/mapspec-runtime` → **778 passed**
- `test/map-render-work-count.test.tsx`（Scenarios A–J work-count 契约）→ **10 passed**
- `tsc --noEmit` → **0 错误**；eslint（22 个变更/相关文件）→ **0 告警**
- 后端 scoped：`pytest tests/cartography/test_mapspec_schema_v6.py tests/cartography/test_ts_projection_contract.py -q` → **26 passed**
- 冒烟（worktree 建立时）：`vitest run lib/mapspec` → **320 passed**
- P7 perf 复测原文：`PERF|compile_10k|13.786`、`PERF|diff_paint_only|0.012`、`PERF|diff_one_changed|0.002`、`PERF|diff_rev_equal|0.005`
- 常量销项 grep：renderer.ts 中 `fill-opacity 0.8`/`circle-radius 6` 字面量 **0 命中**；`return 30` 为契约归一函数保留项（决策 #10）
