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
import { buildLayoutIr, type LayoutIrComponentInput } from './ir';
import { resolveMapComponents } from '@/lib/map-components/resolve-components';
import {
  resolveComponentLayout,
  type LayoutParticipant,
} from '@/lib/map-components/resolve-layout';
import { DEFAULT_COMPONENT_ANCHOR } from '@/lib/map-components/resolve-components';

const CANVAS = { widthPx: 1200, heightPx: 800 };

const COMPONENTS: LayoutIrComponentInput[] = [
  { id: 'title', kind: 'title', role: 'secondary',
    frame: { x: 20, y: 16, width: 500, height: 40, anchor: 'top-left', z: 10 } },
  { id: 'legend', kind: 'legend', role: 'secondary',
    frame: { x: 900, y: 40, width: 260, height: 200, anchor: 'top-right', z: 20 } },
  { id: 'north', kind: 'north_arrow', role: 'decorative',
    frame: { x: 1100, y: 700, width: 60, height: 60, anchor: 'bottom-right', z: 30 } },
  { id: 'scale', kind: 'scale_bar', role: 'decorative',
    frame: { x: 30, y: 730, width: 180, height: 30, anchor: 'bottom-left', z: 30 } },
  { id: 'attr', kind: 'attribution', role: 'decorative',
    frame: { x: 400, y: 760, width: 400, height: 20, anchor: 'bottom-center', z: 30 } },
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
        placement: { mode: 'anchor' as const, anchor: c.frame.anchor },
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
    anchor: String(c.frame.anchor),
    floating: false,
    origin: 'auto' as const,
  }));
  const solved = resolveComponentLayout(participants, CANVAS);
  return new Map([...solved.slots.entries()].map(([id, slot]) => [id, String(slot.slot)]));
}

function typeOf(kind: string): string {
  // IR kind → MapSpecComponent.type（词表交集；测试夹具恒可映射）
  return kind;
}

describe('C2 IR 三渲染器 parity（放置层）', () => {
  it('IR / React DOM / canvas-SVG 三方锚点一致', () => {
    const ir = irOf();
    const dom = domAnchors();
    const slots = solverSlots();
    for (const comp of ir.components) {
      const anchor = comp.frame.anchor;
      expect(dom.get(comp.id)).toBe(anchor);
      // 求解器槽位在无冲突输入下 = 声明锚点（堆叠才会改槽，夹具无堆叠）
      expect(slots.get(comp.id)).toBe(anchor);
    }
  });

  it('Z 序一致：IR layers 升序 ↔ 求解堆叠序（同层按 stackIndex 单调）', () => {
    const ir = irOf();
    const zs = ir.layers.map((l) => l.z);
    expect(zs).toEqual([...zs].sort((a, b) => a - b));
    // 同层组件（z=30 的 north/scale/attr）各占独立槽位 → 槽内 stackIndex
    // 均为 0（stackIndex 是**槽内**序，不是全局层序；语义锁定防误读）
    const participants: LayoutParticipant[] = COMPONENTS.map((c) => ({
      id: c.id, type: typeOf(c.kind), anchor: String(c.frame.anchor),
      floating: false, origin: 'auto' as const,
    }));
    const solved = resolveComponentLayout(participants, CANVAS);
    const bottoms = COMPONENTS.filter((c) => c.frame.z === 30)
      .map((c) => solved.slots.get(c.id));
    expect(bottoms.every((s) => s !== undefined)).toBe(true);
    expect(bottoms.map((s) => s!.index)).toEqual([0, 0, 0]);
    // 同锚堆叠时槽内序递增（确定性）——补一对同锚验证
    const stacked = resolveComponentLayout([
      { id: 'a', type: 'chart_panel', anchor: 'top-left', floating: false, origin: 'auto' },
      { id: 'b', type: 'chart_panel', anchor: 'top-left', floating: false, origin: 'auto' },
    ], CANVAS);
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
