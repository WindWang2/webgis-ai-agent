/**
 * Map components manager — pure helpers over the shared resolution layer
 * (Workspace V2 / Goal C4).
 *
 * The manager enumerates component INSTANCES from the committed MapSpec via
 * `resolveMapComponents` (the single resolution source shared with chrome and
 * export — no second catalog) and expresses every action as a
 * `commitComponentPatch` CAS mutation on the SAME component truth. It holds
 * no state of its own: placements live in MapSpec, optimistic overrides in
 * component-mutation, dock placement in the workspace dock slice (Goal C5 —
 * strictly separate from semantic component state).
 */
import type { ComponentPlacement } from '@/lib/mapspec-compiler/types';
import type { ResolvedMapComponent } from './resolve-components';
import { DEFAULT_COMPONENT_ANCHOR } from './resolve-components';
import type { ComponentPatch } from '@/lib/mapspec/component-mutation';

/** 多实例词表 —— 与后端 component_registry 种子 cardinality=multiple 同表
 *  （图例族 + chart_panel + annotation；catalog JSON 不携带 cardinality，
 *  此处以注释锚定后端源，漂移由 manager 测试锁定）。 */
export const MULTIPLE_INSTANCE_TYPES: ReadonlySet<string> = new Set([
  'legend',
  'categorical_legend',
  'continuous_colorbar',
  'chart_panel',
  'annotation',
]);

/** 管理面词表：chrome 可见组件（map_border/graticule 为画布级装饰，
 *  basemap/export_layout 非地图 chrome 实例 —— 均不进管理列表）。 */
export const MANAGEABLE_TYPES: ReadonlySet<string> = new Set([
  'title',
  'subtitle',
  'north_arrow',
  'scale_bar',
  'attribution',
  'legend',
  'categorical_legend',
  'continuous_colorbar',
  'statistics_panel',
  'chart_panel',
  'annotation',
  'inset_map',
]);

/** zIndex 上限 —— 与后端 ComponentPlacement 有界范围一致（0–200）。 */
export const MAX_COMPONENT_ZINDEX = 200;

/** 面板族（可 dock 的组件类型 —— Goal C5 dock host 消费同一词表）。 */
export const DOCKABLE_PANEL_TYPES: ReadonlySet<string> = new Set([
  'chart_panel',
  'statistics_panel',
]);

export interface ComponentManagerActions {
  show: boolean;
  hide: boolean;
  collapse: boolean;
  expand: boolean;
  resetPosition: boolean;
  bringToFront: boolean;
}

/** 该实例可用动作（纯派生：由类型/当前态决定）。 */
export function availableActions(resolved: ResolvedMapComponent): ComponentManagerActions {
  const hasBody = resolved.type === 'statistics_panel' || resolved.type === 'chart_panel';
  return {
    show: !resolved.enabled,
    hide: resolved.enabled,
    collapse: resolved.enabled && hasBody && !resolved.collapsed,
    expand: resolved.enabled && hasBody && resolved.collapsed,
    resetPosition: resolved.floating,
    bringToFront: resolved.floating,
  };
}

/** Base placement for patches: keep the spec placement when present, else
 *  reconstruct the effective anchored placement from the resolved anchor. */
function basePlacement(resolved: ResolvedMapComponent): ComponentPlacement {
  const spec = resolved.component.placement;
  if (spec && typeof spec === 'object' && (spec.mode === 'floating' || spec.anchor)) {
    return { ...spec };
  }
  return { mode: 'anchor', anchor: resolved.anchor };
}

export function toggleVisibilityPatch(resolved: ResolvedMapComponent): ComponentPatch {
  return { enabled: !resolved.enabled };
}

export function toggleCollapsePatch(resolved: ResolvedMapComponent): ComponentPatch {
  return {
    placement: { ...basePlacement(resolved), collapsed: !resolved.collapsed },
  };
}

/** Reset to the type's default anchor slot (clears user/agent floating coords). */
export function resetPositionPatch(resolved: ResolvedMapComponent): ComponentPatch {
  const anchor = DEFAULT_COMPONENT_ANCHOR[resolved.type] ?? 'top-left';
  return { placement: { mode: 'anchor', anchor } };
}

/** Bring to front: one above the current max zIndex, clamped to the bounded
 *  range (monotonic within a view; callers re-derive per action). */
export function bringToFrontPatch(
  resolved: ResolvedMapComponent,
  currentMaxZ: number,
): ComponentPatch {
  const next = Math.min(MAX_COMPONENT_ZINDEX, Math.max(0, currentMaxZ) + 1);
  return { placement: { ...basePlacement(resolved), mode: 'floating', zIndex: next } };
}

/** 当前所有 floating 实例的最大 zIndex（0 当无 floating）。 */
export function maxFloatingZIndex(components: ResolvedMapComponent[]): number {
  let max = 0;
  for (const c of components) {
    const z = c.floatingRect?.zIndex;
    if (typeof z === 'number' && z > max) max = z;
  }
  return max;
}

/** 管理列表投影：只保留管理面词表内的实例（保持 spec 声明序）。 */
export function manageableComponents(
  components: ResolvedMapComponent[],
): ResolvedMapComponent[] {
  return components.filter((c) => MANAGEABLE_TYPES.has(c.type));
}
