/**
 * Deterministic component layout solver tests (ADR-0084).
 */
import { describe, expect, it } from 'vitest';
import {
  COMPONENT_LAYOUT_META,
  DEFAULT_STACK_STEP_PX,
  resolveComponentLayout,
  stackOffsetPx,
  type LayoutParticipant,
} from './resolve-layout';
import { DEFAULT_COMPONENT_ANCHOR } from './resolve-components';
import catalog from './component-catalog.generated.json';

function p(partial: Partial<LayoutParticipant> & { id: string; type: string }): LayoutParticipant {
  return {
    anchor: (DEFAULT_COMPONENT_ANCHOR[partial.type] ?? 'none') as LayoutParticipant['anchor'],
    floating: false,
    origin: 'auto',
    ...partial,
  };
}

describe('resolveComponentLayout — 槽位与堆叠', () => {
  it('同槽按 priority 堆叠，scale_bar 恒贴边（U-2 约定）', () => {
    const result = resolveComponentLayout([
      p({ id: 'colorbar', type: 'continuous_colorbar' }),   // bottom-right, priority 1
      p({ id: 'scale', type: 'scale_bar' }),                 // bottom-right, priority 0（贴边）
    ]);
    const scale = result.slots.get('scale')!;
    const colorbar = result.slots.get('colorbar')!;
    expect(scale.index).toBe(0);
    expect(colorbar.index).toBe(1);
    expect(scale.slot).toBe('bottom-right');
    expect(colorbar.slotSize).toBe(2);
    // E-1 的核心断言：导出堆叠偏移 > 0（不再与比例尺同点遮挡）
    expect(stackOffsetPx(colorbar, 'continuous_colorbar')).toBeGreaterThan(0);
    expect(stackOffsetPx(scale, 'scale_bar')).toBe(0);
  });

  it('显式 anchor 优先（placement 锚定的 top-right 面板不再被当 top-left）', () => {
    const result = resolveComponentLayout([
      p({ id: 'chart', type: 'chart_panel', anchor: 'top-right' }),
      p({ id: 'stats', type: 'statistics_panel' }), // 默认 top-left
    ]);
    expect(result.slots.get('chart')!.slot).toBe('top-right');
    expect(result.slots.get('stats')!.slot).toBe('top-left');
  });

  it('单槽单组件：index 0 且无偏移', () => {
    const result = resolveComponentLayout([p({ id: 'title', type: 'title' })]);
    const entry = result.slots.get('title')!;
    expect(entry.index).toBe(0);
    expect(entry.slotSize).toBe(1);
    expect(stackOffsetPx(entry, 'title')).toBe(0);
  });

  it('floating/none 不参与锚定堆叠', () => {
    const result = resolveComponentLayout([
      p({ id: 'float-panel', type: 'chart_panel', floating: true, anchor: 'none' }),
      p({ id: 'title', type: 'title' }),
    ]);
    expect(result.slots.has('float-panel')).toBe(false);
    expect(result.slots.has('title')).toBe(true);
  });

  it('确定性：同输入同输出', () => {
    const input = [
      p({ id: 'a', type: 'chart_panel' }),
      p({ id: 'b', type: 'statistics_panel' }),
      p({ id: 'c', type: 'annotation' }),
    ];
    const r1 = resolveComponentLayout(input);
    const r2 = resolveComponentLayout(input);
    expect([...r1.slots.entries()]).toEqual([...r2.slots.entries()]);
    expect(r1.collisions).toEqual(r2.collisions);
  });
});

describe('resolveComponentLayout — user-wins（user > agent > auto）', () => {
  it('user 浮动盒与 auto 锚定组件碰撞 → auto 侧让到 fallback 槽并披露', () => {
    // 画布 1200×800：scale_bar 槽区（estHeight 26）≈ x[928,1188] y[762,788]
    const result = resolveComponentLayout(
      [
        p({ id: 'scale', type: 'scale_bar' }), // auto → bottom-right
        p({
          id: 'user-panel', type: 'chart_panel', floating: true, origin: 'user',
          anchor: 'none',
          rect: { x: 1000, y: 740, width: 180, height: 100 },
        }),
      ],
      { width: 1200, height: 800 },
    );
    const scale = result.slots.get('scale')!;
    expect(scale.fallbackFrom).toBe('bottom-right');
    expect(scale.slot).toBe('bottom-center');
  });

  it('user 浮动盒不挡 auto 组件时：不侧让', () => {
    const result = resolveComponentLayout(
      [
        p({ id: 'scale', type: 'scale_bar' }),
        p({
          id: 'user-panel', type: 'chart_panel', floating: true, origin: 'user',
          anchor: 'none',
          rect: { x: 40, y: 40, width: 160, height: 100 } as never, // 左上角，远离 bottom-right
        }),
      ],
      { width: 1200, height: 800 },
    );
    const scale = result.slots.get('scale')!;
    expect(scale.slot).toBe('bottom-right');
    expect(scale.fallbackFrom).toBeUndefined();
  });

  it('floating × floating 碰撞披露不挪动（user-pinned）', () => {
    const rect = { x: 100, y: 100, width: 150, height: 100 };
    const result = resolveComponentLayout([
      p({ id: 'f1', type: 'chart_panel', floating: true, origin: 'user', anchor: 'none', rect }),
      p({ id: 'f2', type: 'statistics_panel', floating: true, origin: 'user', anchor: 'none', rect: { ...rect, x: 180 } }),
    ]);
    expect(result.collisions).toEqual([
      { kind: 'floating-floating', a: 'f1', b: 'f2' },
    ]);
  });
});

describe('Scenario H fallback 规则：槽高预算容量裁决（v2）', () => {
  const panel = (id: string, type: string, anchor = 'top-left') => ({
    id, type, anchor, floating: false, origin: 'auto' as const,
  });

  it('常规三面板（450px @ 800 画布 ≈ 560 预算）仍同槽堆叠（低扰动）', () => {
    const r = resolveComponentLayout(
      [panel('a', 'annotation'), panel('b', 'statistics_panel'), panel('c', 'chart_panel')],
      { width: 1200, height: 800 },
    );
    expect(r.slots.get('a')!.slot).toBe('top-left');
    expect(r.slots.get('c')!.slot).toBe('top-left');
    expect(r.slots.get('c')!.slotSize).toBe(3);
  });

  it('小画布预算不足 → 最低优先级尾部侧让 fallback 槽并记因', () => {
    // 400px 高 → 预算 280：annotation(90)+statistics(160)=250 可留，
    // chart(200) 超限 → top-left 侧让 top-center
    const r = resolveComponentLayout(
      [panel('a', 'annotation'), panel('b', 'statistics_panel'), panel('c', 'chart_panel')],
      { width: 800, height: 400 },
    );
    expect(r.slots.get('c')!.slot).toBe('top-center');
    expect(r.slots.get('c')!.fallbackFrom).toBe('top-left');
    expect(r.slots.get('a')!.slot).toBe('top-left');
  });

  it('无 fallback 槽可用 / fallback 槽仍超限 → 原槽披露（不三层挪动）', () => {
    // top-right fallback = top-center；制造两个槽都超限
    const r = resolveComponentLayout(
      [
        panel('n', 'north_arrow', 'top-right'),
        panel('i1', 'inset_map', 'top-right'),
        panel('i2', 'inset_map', 'top-right'),  // 假想多 inset：超限候选
        panel('s1', 'statistics_panel', 'top-center'),
        panel('s2', 'chart_panel', 'top-center'),
        panel('s3', 'chart_panel', 'top-center'),
        panel('s4', 'chart_panel', 'top-center'),
      ],
      { width: 800, height: 300 }, // 预算 240
    );
    const collisions = r.collisions.filter((c) => c.kind === 'slot-capacity');
    expect(collisions.length).toBeGreaterThan(0);
  });

  it('确定性：同输入同输出（容量裁决含声明序稳定性）', () => {
    const participants = [
      panel('a', 'annotation'), panel('b', 'statistics_panel'), panel('c', 'chart_panel'),
    ];
    const r1 = resolveComponentLayout(participants, { width: 800, height: 400 });
    const r2 = resolveComponentLayout(participants, { width: 800, height: 400 });
    expect([...r1.slots.entries()]).toEqual([...r2.slots.entries()]);
  });

  it('pass2 原生成员保护：fallback 槽自己的成员永不因并入侧让者被挤出', () => {
    // top-center 有两个本地面板（已胜出）；top-left 超限的 chart 侧让进
    // top-center 后，重排复检不得把本地面板挤出（挤出会 index 落回 0）。
    const r = resolveComponentLayout(
      [
        panel('s1', 'statistics_panel', 'top-center'),
        panel('s2', 'chart_panel', 'top-center'),
        panel('a', 'annotation', 'top-left'),
        panel('c1', 'chart_panel', 'top-left'),
        panel('c2', 'chart_panel', 'top-left'),
      ],
      { width: 800, height: 330 }, // 预算 240：top-left 仅 annotation 留守
    );
    // top-left 只剩 annotation（90px），chart 双双侧让 top-center
    expect(r.slots.get('a')!.slot).toBe('top-left');
    expect(r.slots.get('c1')!.slot).toBe('top-center');
    expect(r.slots.get('c2')!.slot).toBe('top-center');
    // 原生成员保护 + 溢出披露者追加编号：全参与者 (slot, index) 不重号
    const s1 = r.slots.get('s1')!;
    expect(s1.slot).toBe('top-center');
    expect(s1.index).toBe(0); // 原生最高优先级恒贴边
    const seen = new Set<string>();
    for (const [id, res] of r.slots) {
      const key = `${res.slot}#${res.index}`;
      expect(seen.has(key), `${id} duplicate ${key}`).toBe(false);
      seen.add(key);
    }
    // 超限者披露且获得 kept 之后的确定性编号
    expect(r.collisions.some((c) => c.kind === 'slot-capacity')).toBe(true);
    expect(r.slots.get('s2')!.index).toBe(1);
    expect(r.slots.get('c1')!.index).toBe(1);
    expect(r.slots.get('c2')!.index).toBe(2);
  });

  it('user 浮动组件永不因容量被挪动', () => {
    const r = resolveComponentLayout(
      [
        panel('a', 'annotation'),
        panel('b', 'statistics_panel'),
        panel('c', 'chart_panel'),
        { id: 'u', type: 'chart_panel', anchor: 'top-left', floating: true,
          origin: 'user' as const, rect: { x: 10, y: 10, width: 320, height: 240 } },
      ],
      { width: 800, height: 300 },
    );
    expect(r.slots.get('u')).toBeUndefined(); // floating 不在锚定裁决里
  });
});

describe('单一默认值源锁定（E-9）', () => {
  it('DEFAULT_COMPONENT_ANCHOR 与 COMPONENT_LAYOUT_META 与后端 catalog 逐项一致', () => {
    const entries = catalog.componentTypes as Array<{ type: string; defaultPosition: string }>;
    for (const entry of entries) {
      const anchor = DEFAULT_COMPONENT_ANCHOR[entry.type];
      const meta = COMPONENT_LAYOUT_META[entry.type];
      if (anchor === undefined && meta === undefined) continue; // 非布局组件（export_layout 等）
      expect(anchor, `anchor of ${entry.type}`).toBe(entry.defaultPosition);
      expect(meta?.defaultSlot, `meta slot of ${entry.type}`).toBe(entry.defaultPosition);
    }
    // 求解器层距与 live HUD 堆叠约定一致（36px）
    expect(DEFAULT_STACK_STEP_PX).toBe(36);
  });
});
