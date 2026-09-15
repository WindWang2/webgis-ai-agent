/**
 * What-If 推演视图协议映射（ADR-0193）。
 *
 * MapSpec 顶层 `scenario_mode`（schema v1.3，纯 additive）→ 既有
 * ComparisonView 形态词表（workbenchSlice.ComparisonKind）：
 * - `split_view`    → `'side-by-side'`（双栏对照）
 * - `swipe_compare` → `'swipe'`（卷帘对比）
 * - 缺失 / 非法值    → `null`（非推演视图）
 *
 * 本模块是纯函数映射，不持有渲染状态；消费方（map-panel）据其驱动
 * enterComparison / exitComparison。
 */
import type { MapSpec } from '@/lib/mapspec-compiler/types';

/** MapSpec scenario_mode 词表（与 app/lib/cartography/mapspec_schema.py 同源）。 */
export type ScenarioMode = NonNullable<MapSpec['scenario_mode']>;

/** ComparisonView 形态词表（workbenchSlice.ComparisonKind 的结构等价镜像）。 */
export type ComparisonKind = 'side-by-side' | 'swipe';

/** 读取 spec 的推演模式；缺失/非法 → null（非推演视图）。 */
export function scenarioModeOf(spec: MapSpec | null | undefined): ScenarioMode | null {
  const mode = spec?.scenario_mode;
  return mode === 'split_view' || mode === 'swipe_compare' ? mode : null;
}

/** 推演模式 → ComparisonView 形态；null = 应退出对比视图。 */
export function scenarioModeToComparisonKind(
  mode: ScenarioMode | string | null | undefined,
): ComparisonKind | null {
  switch (mode) {
    case 'split_view':
      return 'side-by-side';
    case 'swipe_compare':
      return 'swipe';
    default:
      return null;
  }
}
