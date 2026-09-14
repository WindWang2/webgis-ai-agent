/**
 * C2 IR 三渲染器 parity（V11 W6.1，ADR-0166）。
 *
 * G3 的收敛目标：React DOM（交互）/ canvas（位图导出）/ SVG（矢量导出）
 * 从**同一 IR** 渲染等价结果。本测试锁定**放置层**的三方一致 —— 这是
 * 「等价」中可机器验证且历史上最易分叉的部分（锚点/层级）：
 *
 *   ① IR 面：buildLayoutIr 的 frame.anchor（冻结契约）；
 *   ② React DOM 面：resolveMapComponents（map-spec-chrome 消费的解析器）；
 *   ③ canvas/SVG 面：resolveComponentLayout（export-chrome 及其 SVG 孪生
 *      共用的槽位求解器）→ 生效槽位。
 *
 * 三方对拍 + Z 序（IR layers 升序 ↔ 求解 stackIndex 序）一致性。
 * renderer 像素级 parity（同 IR → 同画面）随各自的渲染接线在 W6 后续
 * 批次追加到本文件（骨架先行，防止收敛结果再分叉）。
 */
import { describe, it, expect } from 'vitest';
import {
  buildLayoutIr,
  type LayoutIrAnchor,
  type LayoutIrComponentInput,
  type LayoutIrComponentKind,
} from './ir';
import { resolveMapComponents } from '@/lib/map-components/resolve-components';
import {
  resolveComponentLayout,
  type LayoutParticipant,
} from '@/lib/map-components/resolve-layout';
import {
  DEFAULT_COMPONENT_ANCHOR,
  type ChromeAnchor,
} from '@/lib/map-components/resolve-components';
import type { MapSpecComponent } from '@/lib/mapspec-compiler/types.generated';

const CANVAS = { widthPx: 1200, heightPx: 800 };

// IR 锚点词表（snake_case 九宫格）→ chrome 渲染面词表（kebab-case 六槽）。
// 两个词表的语义对齐由本转换显式承担（middle_* 在 chrome 面无槽位，夹具不用）。
function toChromeAnchor(anchor: LayoutIrAnchor | undefined): ChromeAnchor {
  const map: Partial<Record<LayoutIrAnchor, ChromeAnchor>> = {
    top_left: 'top-left',
    top_center: 'top-center',
    top_right: 'top-right',
    middle_left: 'none',
    middle_center: 'none',
    middle_right: 'none',
    bottom_left: 'bottom-left',
    bottom_center: 'bottom-center',
    bottom_right: 'bottom-right',
  };
  const mapped = anchor === undefined ? undefined : map[anchor];
  if (mapped === undefined) {
    throw new Error(`no chrome anchor for ${String(anchor)}`);
  }
  return mapped;
}

const COMPONENTS: LayoutIrComponentInput[] = [
  { id: 'title', kind: 'title', role: 'secondary',
    frame: { x: 20, y: 16, width: 500, height: 40, anchor: 'top_left', z: 10 } },
  { id: 'legend', kind: 'legend', role: 'secondary',
    frame: { x: 900, y: 40, width: 260, height: 200, anchor: 'top_right', z: 20 } },
  { id: 'north', kind: 'north_arrow', role: 'decorative',
    frame: { x: 1100, y: 700, width: 60, height: 60, anchor: 'bottom_right', z: 30 } },
  { id: 'scale', kind: 'scale_bar', role: 'decorative',
    frame: { x: 30, y: 730, width: 180, height: 30, anchor: 'bottom_left', z: 30 } },
  { id: 'attr', kind: 'attribution', role: 'decorative',
    frame: { x: 400, y: 760, width: 400, height: 20, anchor: 'bottom_center', z: 30 } },
];

function irOf() {
  return buildLayoutIr({
    canvas: { widthPx: CANVAS.widthPx, heightPx: CANVAS.heightPx },
    components: COMPONENTS,
  });
}

/** 路径 ②：React DOM 解析器（map-spec-chrome 的消费面）。 */
function domAnchors(): Map<string, string> {
  const resolved = resolveMapComponents({
    layout: {
      components: COMPONENTS.map((c) => ({
        id: c.id,
        type: typeOf(c.kind),
        enabled: true,
        placement: { mode: 'anchor' as const, anchor: toChromeAnchor(c.frame.anchor) },
      })),
    },
  });
  return new Map(resolved.map((c) => [c.id, String(c.anchor)]));
}

/** 路径 ③：canvas/SVG 槽位求解器（export-chrome 的消费面）。 */
function solverSlots(): Map<string, string> {
  const participants: LayoutParticipant[] = COMPONENTS.map((c) => ({
    id: c.id,
    type: typeOf(c.kind),
    anchor: toChromeAnchor(c.frame.anchor),
    floating: false,
    origin: 'auto' as const,
  }));
  const solved = resolveComponentLayout(
    participants, { width: CANVAS.widthPx, height: CANVAS.heightPx });
  return new Map([...solved.slots.entries()].map(([id, slot]) => [id, String(slot.slot)]));
}

function typeOf(kind: LayoutIrComponentKind): MapSpecComponent['type'] {
  // IR kind → MapSpecComponent.type：只映射两词表的交集；夹具仅用交集内的
  // kind（author/data_source/text_note 在 chrome 面无对应类型，不可入夹具）。
  const known: Partial<Record<LayoutIrComponentKind, MapSpecComponent['type']>> = {
    title: 'title',
    legend: 'legend',
    north_arrow: 'north_arrow',
    scale_bar: 'scale_bar',
    attribution: 'attribution',
    subtitle: 'subtitle',
    chart_panel: 'chart_panel',
    inset_map: 'inset_map',
    graticule: 'graticule',
  };
  const mapped = known[kind];
  if (mapped === undefined) {
    throw new Error(`fixture kind ${kind} has no MapSpecComponent type`);
  }
  return mapped;
}

describe('C2 IR 三渲染器 parity（放置层）', () => {
  it('IR / React DOM / canvas-SVG 三方锚点一致', () => {
    const ir = irOf();
    const dom = domAnchors();
    const slots = solverSlots();
    for (const comp of ir.components) {
      const anchor = comp.frame.anchor;
      // IR 词表是 snake_case 九宫格，chrome 渲染面是 kebab-case 六槽 ——
      // 经 toChromeAnchor 显式对齐后三方必须一致。
      expect(dom.get(comp.id)).toBe(toChromeAnchor(anchor));
      // 求解器槽位在无冲突输入下 = 声明锚点（堆叠才会改槽，夹具无堆叠）
      expect(slots.get(comp.id)).toBe(toChromeAnchor(anchor));
    }
  });

  it('Z 序一致：IR layers 升序 ↔ 求解堆叠序（同层按 stackIndex 单调）', () => {
    const ir = irOf();
    const zs = ir.layers.map((l) => l.z);
    expect(zs).toEqual([...zs].sort((a, b) => a - b));
    // 同层组件（z=30 的 north/scale/attr）各占独立槽位 → 槽内 stackIndex
    // 均为 0（stackIndex 是**槽内**序，不是全局层序；语义锁定防误读）
    const participants: LayoutParticipant[] = COMPONENTS.map((c) => ({
      id: c.id, type: typeOf(c.kind), anchor: toChromeAnchor(c.frame.anchor),
      floating: false, origin: 'auto' as const,
    }));
    const solved = resolveComponentLayout(
      participants, { width: CANVAS.widthPx, height: CANVAS.heightPx });
    const bottoms = COMPONENTS.filter((c) => c.frame.z === 30)
      .map((c) => solved.slots.get(c.id));
    expect(bottoms.every((s) => s !== undefined)).toBe(true);
    expect(bottoms.map((s) => s!.index)).toEqual([0, 0, 0]);
    // 同锚堆叠时槽内序递增（确定性）——补一对同锚验证
    const stacked = resolveComponentLayout([
      { id: 'a', type: 'chart_panel', anchor: 'top-left', floating: false, origin: 'auto' },
      { id: 'b', type: 'chart_panel', anchor: 'top-left', floating: false, origin: 'auto' },
    ], { width: CANVAS.widthPx, height: CANVAS.heightPx });
    expect(stacked.slots.get('a')!.index).toBe(0);
    expect(stacked.slots.get('b')!.index).toBe(1);
  });

  it('缺省锚点词表一致：DOM 解析器默认与组件锚点表同源', () => {
    const resolved = resolveMapComponents({
      layout: { components: [{ id: 'x', type: 'north_arrow', enabled: true }] },
    });
    expect(String(resolved[0].anchor)).toBe(DEFAULT_COMPONENT_ANCHOR['north_arrow']);
  });
});
