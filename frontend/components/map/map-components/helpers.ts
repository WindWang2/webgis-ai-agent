'use client';
import React from 'react';
import type { MapSpecComponent } from '@/lib/mapspec-compiler/types';

export const POSITION_CLASS: Record<string, string> = {
  'top-left': 'top-3 left-3',
  'top-center': 'top-3 left-1/2 -translate-x-1/2',
  'top-right': 'top-3 right-3',
  'bottom-left': 'bottom-3 left-3',
  'bottom-center': 'bottom-3 left-1/2 -translate-x-1/2',
  'bottom-right': 'bottom-3 right-3',
  none: 'hidden',
};

export const DEFAULT_POSITION: Record<string, string> = {
  title: 'top-center',
  subtitle: 'top-center',
  north_arrow: 'top-right',
  scale_bar: 'bottom-right',
  attribution: 'bottom-left',
  continuous_colorbar: 'bottom-right',
  legend: 'bottom-left',
  categorical_legend: 'bottom-left',
  // 新组件缺省槽位与后端目录（component-catalog.generated.json
  // defaultPosition）保持一致
  annotation: 'top-left',
  statistics_panel: 'top-left',
  chart_panel: 'top-left',
};

export const BOTTOM_OFFSET_STYLE: Record<string, React.CSSProperties> = {
  'bottom-left': { bottom: 'calc(var(--map-chrome-bottom, 10px) + 6px)' },
  'bottom-center': { bottom: 'calc(var(--map-chrome-bottom, 10px) + 6px)' },
  'bottom-right': { bottom: 'calc(var(--map-chrome-bottom, 10px) + 30px)' },
};

export function resolvePosition(component: MapSpecComponent): string {
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

// #1079(G-8)：顶部同槽堆叠 —— chart/statistics/annotation 面板缺省同为
// top-left 且此前无堆叠（只有 bottom 槽实现了分层），两个面板钉同一点
// 互相遮挡。层距与底部一致。
const TOP_STACK_BASE: Record<string, number> = {
  'top-left': 6,
  'top-center': 6,
  'top-right': 6,
};
const TOP_STACK_STEP_PX = 36;

type SlotIndexes = Map<MapSpecComponent, number>;

function buildSlotIndexes(
  renderable: MapSpecComponent[],
  stackBase: Record<string, number>,
  pinTypes: Set<string>,
): SlotIndexes {
  const perSlotCount: Record<string, number> = {};
  const perSlotStacked: Record<string, number> = {};
  for (const c of renderable) {
    const pos = resolvePosition(c);
    if (pos in stackBase) perSlotCount[pos] = (perSlotCount[pos] ?? 0) + 1;
  }
  const indexes: SlotIndexes = new Map();
  for (const c of renderable) {
    const pos = resolvePosition(c);
    if (!(pos in stackBase) || (perSlotCount[pos] ?? 0) <= 1) continue;
    // pinTypes（底部 scale_bar / 顶部 title）恒为槽内第 0 层；其余按
    // spec 声明序 1,2,… 依次偏移。
    if (pinTypes.has(c.type)) {
      indexes.set(c, 0);
    } else {
      perSlotStacked[pos] = (perSlotStacked[pos] ?? 0) + 1;
      indexes.set(c, perSlotStacked[pos]);
    }
  }
  return indexes;
}

export function buildBottomSlotIndexes(
  renderable: MapSpecComponent[],
): SlotIndexes {
  return buildSlotIndexes(renderable, BOTTOM_STACK_BASE, new Set(['scale_bar']));
}

export function buildTopSlotIndexes(
  renderable: MapSpecComponent[],
): SlotIndexes {
  return buildSlotIndexes(renderable, TOP_STACK_BASE, new Set(['title']));
}

export function stackedBottomStyle(
  component: MapSpecComponent,
  slotIndexes?: SlotIndexes,
): React.CSSProperties | undefined {
  const pos = resolvePosition(component);
  const base = BOTTOM_STACK_BASE[pos];
  if (base === undefined) {
    return stackedTopStyle(component, slotIndexes) ?? BOTTOM_OFFSET_STYLE[pos];
  }
  const idx = slotIndexes?.get(component) ?? 0;
  return { bottom: `calc(var(--map-chrome-bottom, 10px) + ${base + idx * BOTTOM_STACK_STEP_PX}px)` };
}

export function stackedTopStyle(
  component: MapSpecComponent,
  slotIndexes?: SlotIndexes,
): React.CSSProperties | undefined {
  const pos = resolvePosition(component);
  const base = TOP_STACK_BASE[pos];
  if (base === undefined) return undefined;
  const idx = slotIndexes?.get(component) ?? 0;
  return { top: `calc(var(--map-chrome-top, 10px) + ${base + idx * TOP_STACK_STEP_PX}px)` };
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
