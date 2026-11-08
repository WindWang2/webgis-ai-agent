/**
 * G04 W1（#1999）：导出时约束版面应用（applyConstraintLayoutToSpec）。
 *
 * 契约（纯函数，导出副本专用 —— 持久 MapSpec 零触碰）：
 * - evaluated 报告：placement_zones 写回组件 position/anchor（浮动件跳过）；
 *   suppressed_ids（非 key/非锁）→ enabled=false；
 * - 用户锁（workbench.lockedComponentIds，含 `__` 族语义）零触碰；
 * - not_evaluated / 非法输入 → 原样返回（同一引用，不复制）。
 */
import { describe, expect, it } from 'vitest';
import {
  applyConstraintLayoutToSpec,
  type ConstraintLayoutReportFE,
} from './constraint-layout-apply';

type Comp = Record<string, unknown>;

function spec(components: Comp[], locked: string[] = []): Record<string, unknown> {
  return {
    version: '1.6',
    workbench: { lockedComponentIds: locked },
    layout: { components },
  };
}

function evaluatedReport(
  zones: Record<string, string>,
  suppressed: string[] = [],
  keys: string[] = [],
): ConstraintLayoutReportFE {
  return {
    constraint_layout: 'evaluated',
    page_profile: 'a4_landscape',
    placement_zones: zones,
    suppressed_ids: suppressed,
    key_component_ids: keys,
  };
}

describe('applyConstraintLayoutToSpec · 求解区域写回', () => {
  it('placement_zones 写回 position 与 placement.anchor（模式 anchor 时）', () => {
    const input = spec([
      { id: 'legend-1', type: 'legend', enabled: true, position: 'bottom-left',
        placement: { mode: 'anchor', anchor: 'bottom-left' } },
    ]);
    const out = applyConstraintLayoutToSpec(
      input,
      evaluatedReport({ 'legend-1': 'top-right' }),
    ) as typeof input;
    expect(out).not.toBe(input); // 导出副本
    const legend = (out.layout as { components: Comp[] }).components[0];
    expect(legend.position).toBe('top-right');
    expect((legend.placement as { anchor?: string }).anchor).toBe('top-right');
    // 输入原样（持久面零触碰）
    const original = (input.layout as { components: Comp[] }).components[0];
    expect(original.position).toBe('bottom-left');
  });

  it('浮动件与未知 id 不动', () => {
    const input = spec([
      { id: 'f-1', type: 'chart_panel', enabled: true,
        placement: { mode: 'floating', x: 10, y: 20 } },
      { id: 'ghost', type: 'legend', enabled: true, position: 'bottom-left' },
    ]);
    const out = applyConstraintLayoutToSpec(
      input,
      evaluatedReport({ 'f-1': 'top-left', 'ghost-2': 'top-right' }),
    ) as typeof input;
    const comps = (out.layout as { components: Comp[] }).components;
    expect(comps[0].position).toBeUndefined();
    expect(comps[1].position).toBe('bottom-left');
  });

  it('suppressed_ids 非关键组件 → enabled=false；key 组件保持', () => {
    const input = spec([
      { id: 'ann-1', type: 'annotation', enabled: true, position: 'top-left' },
      { id: 'note-1', type: 'methodology_note', enabled: true, position: 'bottom-center' },
    ]);
    const out = applyConstraintLayoutToSpec(
      input,
      evaluatedReport({}, ['ann-1', 'note-1'], ['note-1']),
    ) as typeof input;
    const comps = (out.layout as { components: Comp[] }).components;
    expect(comps[0].enabled).toBe(false);
    expect(comps[1].enabled).toBe(true);
  });

  it('浮动件即便在 suppressed_ids 中也保持在场(与后端矢量链同语义)', () => {
    const input = spec([
      { id: 'float-chart', type: 'chart_panel', enabled: true,
        placement: { mode: 'floating', x: 12, y: 24 } },
      { id: 'docked-ann', type: 'annotation', enabled: true, position: 'top-left' },
    ]);
    const out = applyConstraintLayoutToSpec(
      input,
      evaluatedReport({}, ['float-chart', 'docked-ann']),
    ) as typeof input;
    const comps = (out.layout as { components: Comp[] }).components;
    expect(comps[0].enabled).toBe(true);   // 浮动件豁免抑制
    expect(comps[1].enabled).toBe(false);  // 锚定件照常抑制
  });

  it('用户锁（含 __ 族派生实例）零触碰：不挪位、不抑制', () => {
    const input = spec([
      { id: 'zz-chart', type: 'chart_panel', enabled: true, position: 'bottom-right' },
      { id: 'zz-chart__2', type: 'chart_panel', enabled: true, position: 'bottom-right' },
    ], ['zz-chart']);
    const out = applyConstraintLayoutToSpec(
      input,
      evaluatedReport(
        { 'zz-chart': 'top-left', 'zz-chart__2': 'top-left' },
        ['zz-chart__2'],
      ),
    ) as typeof input;
    const comps = (out.layout as { components: Comp[] }).components;
    expect(comps[0].position).toBe('bottom-right');
    expect(comps[1].position).toBe('bottom-right');
    expect(comps[1].enabled).toBe(true);
  });
});

describe('applyConstraintLayoutToSpec · 诚实缺省', () => {
  it('not_evaluated → 同一引用返回', () => {
    const input = spec([{ id: 'a', type: 'legend', enabled: true }]);
    const report: ConstraintLayoutReportFE = {
      constraint_layout: 'not_evaluated',
      reason: 'page_profile_absent',
    };
    expect(applyConstraintLayoutToSpec(input, report)).toBe(input);
    expect(applyConstraintLayoutToSpec(input, null)).toBe(input);
    expect(applyConstraintLayoutToSpec(input, undefined)).toBe(input);
  });

  it('报告缺 zones/suppressed → 只应用在场部分', () => {
    const input = spec([
      { id: 'a', type: 'legend', enabled: true, position: 'bottom-left' },
    ]);
    const out = applyConstraintLayoutToSpec(input, {
      constraint_layout: 'evaluated',
      page_profile: 'a4_landscape',
    }) as typeof input;
    expect(out).not.toBe(input);
    const comps = (out.layout as { components: Comp[] }).components;
    expect(comps[0].position).toBe('bottom-left');
    expect(comps[0].enabled).toBe(true);
  });
});
