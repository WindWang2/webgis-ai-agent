/**
 * Deterministic component layout solver (ADR-0084 Cartographic Layout Engine).
 *
 * 单一布局求解器：live chrome（helpers 的 top/bottom 堆叠）与 canvas
 * exporter（margin 偏移）此前各有一套半成品 —— live 底槽有 U-2 堆叠但顶槽
 * 只认旧 position 字段，导出完全没有堆叠（scale_bar 与 colorbar 同锚
 * bottom-right 互相遮挡）。本模块是两侧共用的**纯函数**槽位求解层：
 *
 * 输入：组件参与者（id/type/解析后的 anchor/floating 矩形/origin）
 * 输出：每组件 (slot, index, slotSize[, fallbackFrom]) + 碰撞披露
 *
 * 契约：
 * - 确定性：同输入同输出（排序键 (priority, 声明序)）；
 * - user > agent > auto：floating（user-pinned）组件不参与堆叠也不被挪动，
 *   auto 锚定组件与 user 浮动盒碰撞时**自动侧让**（fallback 槽，披露
 *   fallbackFrom），绝不覆盖用户拖放结果；
 * - anchor 已由调用方解析（resolveMapComponent.anchor / resolvePosition）
 *   —— 本模块不做 anchor 优先级裁决（那是 resolver 层的职责，单一层做
 *   一件事）；'none'/floating 不参与锚定堆叠；
 * - scale_bar 恒贴边（U-2/#884 约定），槽内其余按 priority 远离边。
 */

import type { ChromeAnchor } from './resolve-components';

export type LayoutSlot =
  | 'top-left' | 'top-center' | 'top-right'
  | 'bottom-left' | 'bottom-center' | 'bottom-right'
  | 'none';

export interface ComponentLayoutMeta {
  defaultSlot: LayoutSlot;
  /** 槽内堆叠序（小者更贴边）。 */
  priority: number;
  /** 同槽语义独占标记（信息性 —— 求解器不丢弃组件，只保证排序稳定）。 */
  exclusive?: boolean;
  stackStepPx?: number;
}

export const DEFAULT_STACK_STEP_PX = 36;

/** 组件类型布局元数据（与 component-catalog defaultPosition 对齐，测试锁定）。 */
export const COMPONENT_LAYOUT_META: Record<string, ComponentLayoutMeta> = {
  title: { defaultSlot: 'top-center', priority: 0, exclusive: true },
  subtitle: { defaultSlot: 'top-center', priority: 1 },
  north_arrow: { defaultSlot: 'top-right', priority: 0 },
  scale_bar: { defaultSlot: 'bottom-right', priority: 0 },
  attribution: { defaultSlot: 'bottom-left', priority: 0 },
  continuous_colorbar: { defaultSlot: 'bottom-right', priority: 1 },
  legend: { defaultSlot: 'bottom-left', priority: 1 },
  categorical_legend: { defaultSlot: 'bottom-left', priority: 2 },
  annotation: { defaultSlot: 'top-left', priority: 0 },
  statistics_panel: { defaultSlot: 'top-left', priority: 1 },
  chart_panel: { defaultSlot: 'top-left', priority: 2 },
  // v2：区位插图（与 north_arrow 同占 top-right 族，槽内按 priority 堆叠）
  inset_map: { defaultSlot: 'top-right', priority: 3 },
  // P6：全画布/叠加型 —— 无槽（求解器排除，catalog defaultPosition='none'）
  map_border: { defaultSlot: 'none', priority: 70 },
  graticule: { defaultSlot: 'none', priority: 60 },
};

/** auto 锚定组件与 user 浮动盒碰撞时的侧让槽（确定性、单步）。 */
const ANCHOR_FALLBACK: Partial<Record<LayoutSlot, LayoutSlot>> = {
  'bottom-right': 'bottom-center',
  'bottom-left': 'bottom-center',
  'bottom-center': 'bottom-left',
  'top-left': 'top-center',
  'top-right': 'top-center',
  'top-center': 'top-left',
};

export interface LayoutParticipant {
  id: string;
  type: string;
  /** 解析后的锚点（resolver 层产物；floating 组件填兜底 anchor）。 */
  anchor: ChromeAnchor;
  /** user-pinned 浮动组件（不参与堆叠、不被挪动）。 */
  floating: boolean;
  /** floating 逻辑像素盒（碰撞判定用；缺 width/height 时跳过）。 */
  rect?: { x: number; y: number; width?: number; height?: number };
  /** 放置来源：user 浮动 > agent 显式 > auto 默认。 */
  origin: 'auto' | 'agent' | 'user';
}

export interface SlotResolution {
  slot: LayoutSlot;
  /** 槽内堆叠序（0 贴边）。单组件槽也赋 0。 */
  index: number;
  /** 槽内组件总数（≤1 时消费方无需偏移）。 */
  slotSize: number;
  /** 侧让前的原槽（仅发生 user-浮动碰撞侧让时出现）。 */
  fallbackFrom?: LayoutSlot;
}

export interface LayoutCollision {
  kind: 'floating-floating' | 'floating-anchor';
  a: string;
  b: string;
}

export interface ComponentLayoutResult {
  /** id → 槽位裁决（floating/none 组件不在其中）。 */
  slots: Map<string, SlotResolution>;
  collisions: LayoutCollision[];
}

interface Box { x: number; y: number; w: number; h: number }

function _aabb(a: Box, b: Box): boolean {
  return a.x < b.x + b.w && b.x < a.x + a.w && a.y < b.y + b.h && b.y < a.y + a.h;
}

/**
 * 确定性求解（纯函数）。
 *
 * 1. floating 组件互检碰撞（披露不挪动 —— user-pinned）；
 * 2. auto 锚定组件的槽区与 user 浮动盒重叠 → 侧让到 ANCHOR_FALLBACK
 *    槽（agent 显式锚定同样侧让 —— 用户的画布占用优先）；
 * 3. 槽内按（priority, 声明序）赋 index；scale_bar 恒 index 0（贴边）。
 */
export function resolveComponentLayout(
  participants: LayoutParticipant[],
  canvas?: { width: number; height: number },
  opts?: { marginPx?: number; slotWidthPx?: number },
): ComponentLayoutResult {
  const margin = opts?.marginPx ?? 12;
  const slotW = opts?.slotWidthPx ?? 260;
  const collisions: LayoutCollision[] = [];

  // 1. floating × floating 碰撞（user-pinned 只披露）
  const floats = participants.filter(
    (p) => p.floating && p.rect && (p.rect.width ?? 0) > 0 && (p.rect.height ?? 0) > 0,
  );
  for (let i = 0; i < floats.length; i++) {
    for (let j = i + 1; j < floats.length; j++) {
      const a = floats[i];
      const b = floats[j];
      if (
        _aabb(
          { x: a.rect!.x, y: a.rect!.y, w: a.rect!.width ?? 0, h: a.rect!.height ?? 0 },
          { x: b.rect!.x, y: b.rect!.y, w: b.rect!.width ?? 0, h: b.rect!.height ?? 0 },
        )
      ) {
        collisions.push({ kind: 'floating-floating', a: a.id, b: b.id });
      }
    }
  }

  // 2. auto 锚定槽区 vs user 浮动盒 → 侧让（估算槽区盒：贴边 260×160）
  const slotOf = (p: LayoutParticipant): LayoutSlot | null => {
    if (p.floating) return null;
    const a = p.anchor as LayoutSlot;
    return a === 'none' ? null : a;
  };
  const userBoxes: Box[] = floats
    .filter((p) => p.origin === 'user')
    .map((p) => ({ x: p.rect!.x, y: p.rect!.y, w: p.rect!.width ?? 0, h: p.rect!.height ?? 0 }));

  const estHeight: Record<string, number> = {
    title: 48, subtitle: 28, north_arrow: 56, scale_bar: 26,
    attribution: 18, continuous_colorbar: 120, legend: 140,
    categorical_legend: 140, annotation: 90, statistics_panel: 160,
    chart_panel: 200, inset_map: 168,
  };
  const slotBox = (slot: LayoutSlot, h: number): Box => {
    const W = canvas?.width ?? 1200;
    const H = canvas?.height ?? 800;
    const w = Math.min(slotW, W / 3);
    const x =
      slot.endsWith('left') ? margin
        : slot.endsWith('right') ? W - margin - w
          : W / 2 - w / 2;
    const y = slot.startsWith('top') ? margin : H - margin - h;
    return { x, y, w, h };
  };

  const resolvedAnchor = new Map<string, { slot: LayoutSlot; from?: LayoutSlot }>();
  for (const p of participants) {
    const slot = slotOf(p);
    if (!slot) continue;
    const wantsFallback =
      userBoxes.length > 0 && p.origin !== 'user';
    if (wantsFallback) {
      const h = estHeight[p.type] ?? 90;
      const box = slotBox(slot, h);
      if (userBoxes.some((ub) => _aabb(box, ub))) {
        const fb = ANCHOR_FALLBACK[slot];
        if (fb) {
          resolvedAnchor.set(p.id, { slot: fb, from: slot });
          continue;
        }
      }
    }
    resolvedAnchor.set(p.id, { slot });
  }

  // 3. 槽内 (priority, 声明序) 赋 index；scale_bar 恒贴边
  const groups = new Map<LayoutSlot, LayoutParticipant[]>();
  for (const p of participants) {
    const entry = resolvedAnchor.get(p.id);
    if (!entry) continue;
    const bucket = groups.get(entry.slot);
    if (bucket) bucket.push(p);
    else groups.set(entry.slot, [p]);
  }
  const ordered = new Map<LayoutSlot, LayoutParticipant[]>();
  for (const [slot, bucket] of groups) {
    const sorted = [...bucket].sort((a, b) => {
      const pa = a.type === 'scale_bar' ? -1 : (COMPONENT_LAYOUT_META[a.type]?.priority ?? 50);
      const pb = b.type === 'scale_bar' ? -1 : (COMPONENT_LAYOUT_META[b.type]?.priority ?? 50);
      return pa !== pb ? pa - pb : 0; // 稳定排序保声明序
    });
    ordered.set(slot, sorted);
  }

  const slots = new Map<string, SlotResolution>();
  for (const p of participants) {
    const entry = resolvedAnchor.get(p.id);
    if (!entry) continue;
    const bucket = ordered.get(entry.slot) ?? [];
    slots.set(p.id, {
      slot: entry.slot,
      index: Math.max(0, bucket.indexOf(p)),
      slotSize: bucket.length,
      fallbackFrom: entry.from,
    });
    // 槽区（含侧让后）与 user 浮动盒仍重叠 → 披露（不强制挪动）
    if (userBoxes.length > 0) {
      const h = estHeight[p.type] ?? 90;
      const box = slotBox(entry.slot, h);
      if (userBoxes.some((ub) => _aabb(box, ub))) {
        collisions.push({ kind: 'floating-anchor', a: p.id, b: 'user-floating' });
      }
    }
  }

  return { slots, collisions };
}

/** 消费端换算：槽位堆叠偏移（bottom 槽向上远离边、top 槽向下远离边）。 */
export function stackOffsetPx(
  resolution: SlotResolution,
  type: string,
): number {
  if (resolution.slotSize <= 1 || resolution.index <= 0) return 0;
  const step = COMPONENT_LAYOUT_META[type]?.stackStepPx ?? DEFAULT_STACK_STEP_PX;
  return resolution.index * step;
}

/**
 * v2（Scenario H）：floating 盒确定性地夹取进画布 —— 小视口/移动端上
 * 用户拖出界或历史像素越界的组件不得无限溢出（导出画布同理）。
 * 纯函数、同输入同输出：x/y 夹取到 [8, canvas-最小可见 96px] 窗口，
 * 宽高夹取到画布上界（面板不允许大于画布）。语义语义一致性：这是
 * 渲染期的视口约束（derived），不改 MapSpec 的语义 placement。
 */
export function clampFloatingRect(
  rect: { x: number; y: number; width?: number; height?: number },
  canvas: { width: number; height: number },
): { x: number; y: number; width?: number; height?: number } {
  const MIN_VISIBLE = 96;
  const EDGE = 8;
  const w = rect.width ?? 0;
  const h = rect.height ?? 0;
  const maxX = Math.max(EDGE, canvas.width - Math.max(MIN_VISIBLE, Math.min(w, MIN_VISIBLE)) - EDGE);
  const maxY = Math.max(EDGE, canvas.height - Math.max(MIN_VISIBLE, Math.min(h, MIN_VISIBLE)) - EDGE);
  const out: { x: number; y: number; width?: number; height?: number } = {
    x: Math.min(Math.max(rect.x, EDGE), maxX),
    y: Math.min(Math.max(rect.y, EDGE), maxY),
  };
  if (rect.width !== undefined) {
    out.width = Math.min(rect.width, Math.max(0, canvas.width - out.x - EDGE));
  }
  if (rect.height !== undefined) {
    out.height = Math.min(rect.height, Math.max(0, canvas.height - out.y - EDGE));
  }
  return out;
}
