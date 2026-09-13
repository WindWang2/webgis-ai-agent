# AC-07 交付台账（任务 → 文件 → 测试 → 证据）

> ADR-0156 · 分支 adaptive-cartography/07-layout-auto-compose

## P0 勘察（只读）

| 交付物 | 文件 | 证据 |
|---|---|---|
| 组件矩阵（20 类型/19 descriptor/18 渲染器真值） | docs/dev/ac-07-component-matrix.csv | 每行 evidence 列（文件:行） |
| 20 MapSpec × 4 版式 × 2 口径告警基线 | docs/dev/ac-07-baseline.json + ac-07-layout-recon.md §4 | native 72/80 LAYOUT_COLLISION；corrupt +80/80 OUTSIDE |
| fallback 触发条件与频率 | ac-07-layout-recon.md §5 | 四通道表；corpus 域 0%（compose 必带 chrome） |
| solver 能力边界 | ac-07-layout-recon.md §6 | V2/V3 = 选位；无自愈/断环/决策工件 |

## P1 冲突自愈

| 任务 | 文件 | 测试 |
|---|---|---|
| V4 求解器（策略链四级） | app/lib/cartography/layout_solver.py（solve_layout_v4 纯增量） | tests/unit/test_layout_selfheal_ac07.py::test_v4_*（等价性/四级/单例保护/required 保留） |
| 修复规划器 | app/lib/cartography/component_composer.py（plan_layout_repairs / floating_overlap_pairs） | 同上 ::test_plan_repairs_* |
| 断环策略 | app/lib/cartography/component_graph.py（break_component_cycles） | 同上 ::test_break_*（最低权重/字典序平局/双环/无环 no-op） |
| QA 建议接线 | app/lib/cartography/semantic_checks.py（仅 LAYOUT_COLLISION / COMPONENT_LINK_CYCLE 建议段；floating 几何单一化） | 同上 ::test_layout_collision_warning_carries_repair_suggestion / ::test_link_cycle_fail_carries_break_suggestion |
| 前端执行器 | frontend/lib/layout/composition-repair.ts | frontend/lib/layout/compose.test.ts::planCompositionRepairs |
| 归零门禁 | tests/cartography/test_ac07_zero_regression.py | 修复后三类检查 0 残留（native+corrupt × 4 版式） |

## P2 缺项自动补全

| 任务 | 文件 | 测试 |
|---|---|---|
| required_components_for（后端） | component_composer.py | test_layout_selfheal_ac07.py::test_required_components_* |
| 前端镜像 + autofill 执行器 | frontend/lib/layout/required-components.ts | compose.test.ts::requiredComponentsFor |
| 编排（chrome/export 对接口） | frontend/lib/layout/compose.ts + map-spec-chrome.tsx 接线 | compose.test.ts::composeMapLayout（autofill/fallback/disabled/占位/provenance） |

## P3–P5 能力升级

| 任务 | 文件 | 测试 |
|---|---|---|
| P3 数字比例尺 | frontend/lib/layout/numeric-scale.ts + scale-bar.tsx 接线 | layout-math.test.ts（赤道/中纬/高纬 ≤5% 对照闭式 + 纬度修正 + 模式解析）；ac07-layout-renderers.test.tsx（并存/numeric 模式/纬度敏感） |
| P4 图廓注记 + 磁偏角 | frontend/lib/layout/graticule-labels.ts + magnetic-declination.ts + graticule/north-arrow 接线 | layout-math.test.ts（三档格式/四角注记/偏角区域合理性/归一化）；ac07-layout-renderers.test.tsx（偏角注记/可关/缺席不虚构） |
| P5 密度自适应 | frontend/lib/layout/graticule-density.ts + graticule.tsx 接线 | layout-math.test.ts（6 档跨度 [3,10]/极端跨度/显式覆盖/zoom 回退）；ac07-layout-renderers.test.tsx（data 属性披露/覆盖优先） |

## P6–P7

| 任务 | 文件 | 测试 |
|---|---|---|
| P6 inset_map 真值钉住 | app/services/gis_harness/components.py（注释漂移修正）；tests/unit/test_inset_map_native_ac07.py | native 状态/支持矩阵/类型注册/required_context 四项；前端 source 隔离静态扫描（ac07-layout-renderers.test.tsx） |
| P7 图例 v2 统一 | frontend/lib/layout/legend-labels.ts + legends.tsx/colorbar.tsx 接线 | compose.test.ts::legend-labels（优先级链/k 推断/v1 回退）；ac07-layout-renderers.test.tsx（fixture 驱动 DOM 断言：unit/method/nodata 覆写/out_of_range/色条 nodata 色块） |

## P8 回归与收口

| 门禁 | 结果 |
|---|---|
| 20×4 归零（corpus 全量口径） | scripts/ac07_p0_baseline.py：after_repairs_native={} after_repairs_corrupt={}（docs/dev/ac-07-baseline.json） |
| 前端 vitest（components/map + lib/map-kit + lib/layout） | 78 文件 / 720 测试全绿（04:42:50） |
| 后端 pytest tests/unit（not heavy/real_services/perf） | 见 PR 本地门禁证据（串行，同 CI 参数） |
| ruff check（9 变更文件） | All checks passed! |
| eslint（8 变更文件 + lib/layout） | 0 problems |
| tsc --noEmit + next build | 见 PR（P8 唯一一次 build） |
