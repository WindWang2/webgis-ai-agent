'use client';
import React from 'react';
import type { MapSpecComponent } from '@/lib/mapspec-compiler/types';
import {
  COMPONENT_LAYOUT_META,
  DEFAULT_STACK_STEP_PX,
  resolveSlotLayout,
} from '@/lib/map-components/layout-runtime';

export const POSITION_CLASS: Record<string, string> = {
  'top-left': 'top-3 left-3',
  'top-center': 'top-3 left-1/2 -translate-x-1/2',
  'top-right': 'top-3 right-3',
  'bottom-left': 'bottom-3 left-3',
  'bottom-center': 'bottom-3 left-1/2 -translate-x-1/2',
  'bottom-right': 'bottom-3 right-3',
  none: 'hidden',
};

// ADR-0081：缺省槽位表与共享 resolver（live/export 同源）同一份 —— 不再
// 维护第二张字面量表（三表漂移是 review P1 的根因）。
import { DEFAULT_COMPONENT_ANCHOR } from '@/lib/map-components/resolve-components';

export const DEFAULT_POSITION: Record<string, string> = DEFAULT_COMPONENT_ANCHOR;

export const BOTTOM_OFFSET_STYLE: Record<string, React.CSSProperties> = {
  'bottom-left': { bottom: 'calc(var(--map-chrome-bottom, 10px) + 6px)' },
  'bottom-center': { bottom: 'calc(var(--map-chrome-bottom, 10px) + 6px)' },
  'bottom-right': { bottom: 'calc(var(--map-chrome-bottom, 10px) + 30px)' },
};

/**
 * anchor 解析优先级与共享 resolver 一致（ADR-0081 review P1）：
 * placement.mode=anchor 的 anchor > 旧 position 字段 > 类型默认槽位。
 * 此前 live 不读 placement.anchor —— spec 里 anchor 与 position 不一致
 * （双写契约被绕过）时 live/export 各画各的，正是 parity 要消除的分叉。
 */
export function resolvePosition(component: MapSpecComponent): string {
  const placement = component.placement;
  if (placement?.mode === 'anchor' && typeof placement.anchor === 'string' && placement.anchor) {
    return placement.anchor;
  }
  return (component as unknown as { position?: string }).position ?? DEFAULT_POSITION[component.type] ?? 'none';
}

export function positionClass(component: MapSpecComponent): string {
  const pos = resolvePosition(component);
  return POSITION_CLASS[pos] ?? POSITION_CLASS[DEFAULT_POSITION[component.type] ?? 'none'] ?? 'hidden';
}

export function positionStyle(component: MapSpecComponent): React.CSSProperties | undefined {
  return BOTTOM_OFFSET_STYLE[resolvePosition(component)];
}

// U-2（#884）：底部同槽多组件按「scale_bar 最贴底、其余按 spec 序上移」
// 分层堆叠 —— 色条与比例尺缺省同为 bottom-right 且偏移相同，两个元素会
// 锚定同一点互相遮挡。层距 36px 对齐 HUD chrome 族（状态读数 +0 / 比例尺
// +30 / 图例 +66）的堆叠约定。
const BOTTOM_STACK_BASE: Record<string, number> = {
  'bottom-left': 6,
  'bottom-center': 6,
  'bottom-right': 30,
};
const BOTTOM_STACK_STEP_PX = 36;

export function buildBottomSlotIndexes(
  renderable: MapSpecComponent[],
): Map<MapSpecComponent, number> {
  const perSlotCount: Record<string, number> = {};
  const perSlotStacked: Record<string, number> = {};
  for (const c of renderable) {
    const pos = resolvePosition(c);
    if (pos in BOTTOM_STACK_BASE) perSlotCount[pos] = (perSlotCount[pos] ?? 0) + 1;
  }
  const indexes = new Map<MapSpecComponent, number>();
  for (const c of renderable) {
    const pos = resolvePosition(c);
    if (!(pos in BOTTOM_STACK_BASE) || (perSlotCount[pos] ?? 0) <= 1) continue;
    // scale_bar 恒为槽内第 0 层（贴底）；其余组件按 spec 声明序 1,2,… 上移
    if (c.type === 'scale_bar') {
      indexes.set(c, 0);
    } else {
      perSlotStacked[pos] = (perSlotStacked[pos] ?? 0) + 1;
      indexes.set(c, perSlotStacked[pos]);
    }
  }
  return indexes;
}

export function stackedBottomStyle(
  component: MapSpecComponent,
  slotIndexes?: Map<MapSpecComponent, number>,
): React.CSSProperties | undefined {
  const pos = resolvePosition(component);
  const base = BOTTOM_STACK_BASE[pos];
  if (base === undefined) return BOTTOM_OFFSET_STYLE[pos];
  const idx = slotIndexes?.get(component) ?? 0;
  return { bottom: `calc(var(--map-chrome-bottom, 10px) + ${base + idx * BOTTOM_STACK_STEP_PX}px)` };
}

// ── D1/D4：placement 自由布局原语 ────────────────────────────────────────
// floating 模式渲染器组合方式：`absolute` 类 + placementStyle 内联样式；
// anchor 模式走旧 positionClass 槽位（placementStyle 返回 undefined，
// 既有槽位类/堆叠偏移完全不受影响）。

/** floating 布局判定（anchor / 缺省 placement 均走旧槽位语义）。 */
export function isFloating(component: MapSpecComponent): boolean {
  return component.placement?.mode === 'floating';
}

/** floating 布局内联样式；anchor 模式返回 undefined（旧槽位类继续生效）。 */
export function placementStyle(component: MapSpecComponent): React.CSSProperties | undefined {
  const placement = component.placement;
  if (!placement || placement.mode !== 'floating') return undefined;
  const style: React.CSSProperties = {
    left: typeof placement.x === 'number' ? Math.round(placement.x) : undefined,
    top: typeof placement.y === 'number' ? Math.round(placement.y) : undefined,
    // 缺省 zIndex=40：浮动面板盖过锚定 chrome（z-30），压不过弹层
    zIndex: typeof placement.zIndex === 'number' ? placement.zIndex : 40,
  };
  if (typeof placement.width === 'number') style.width = placement.width;
  if (typeof placement.height === 'number') style.height = placement.height;
  return style;
}

/**
 * 组件 variant 解析：options.variant（历史载体，north_arrow 等）优先，
 * 其次 component.variant（目录字段），缺省回退 fallback。
 */
export function resolveVariant(component: MapSpecComponent, fallback = ''): string {
  const fromOptions = component.options?.['variant'];
  if (typeof fromOptions === 'string' && fromOptions) return fromOptions;
  if (typeof component.variant === 'string' && component.variant) return component.variant;
  return fallback;
}

// ── v2(Phase 9, #1079)：顶槽堆叠原语（与 bottom U-2 对称）───────────────
// chart/statistics/annotation 缺省同为 top-left —— 无堆叠时锚定同一点互相
// 遮挡。槽位/优先级元数据统一在 layout-runtime（ComponentLayoutRuntime）。

export function buildTopSlotIndexes(
  renderable: MapSpecComponent[],
): Map<MapSpecComponent, number> {
  const indexes = new Map<MapSpecComponent, number>();
  for (const [component, entry] of resolveSlotLayout(renderable)) {
    if (entry.slot.startsWith('top-') && entry.slotSize > 1) {
      indexes.set(component, entry.index);
    }
  }
  return indexes;
}

export function stackedTopStyle(
  component: MapSpecComponent,
  topSlotIndexes?: Map<MapSpecComponent, number>,
): React.CSSProperties | undefined {
  const pos = resolvePosition(component);
  if (!pos.startsWith('top-')) return undefined;
  const idx = topSlotIndexes?.get(component) ?? 0;
  if (idx === 0) return undefined;
  const step = COMPONENT_LAYOUT_META[component.type]?.stackStepPx ?? DEFAULT_STACK_STEP_PX;
  return { top: `calc(0.75rem + ${idx * step}px)` };
}
