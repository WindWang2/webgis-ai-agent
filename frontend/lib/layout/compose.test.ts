/**
 * AC-07（ADR-0156）：composition-repair 策略链 + composeMapLayout 编排 +
 * required-components（P2 缺项补全）单测。
 */
import { describe, expect, it } from 'vitest';

import type { LayoutParticipant } from '@/lib/map-components/resolve-layout';

import { planCompositionRepairs, repairStepsToDecisions } from './composition-repair';
import { composeMapLayout, AUTOINJECTABLE_TYPES } from './compose';
import {
  autofillComponents,
  DATA_SOURCE_PLACEHOLDER_TEXT,
  requiredComponentsFor,
} from './required-components';
import {
  DEFAULT_NODATA_LABEL,
  legendClassCount,
  legendMethodLabel,
  legendNodataLabel,
  legendOutOfRangeLabel,
  legendUnitSuffix,
} from './legend-labels';
import type { MapSpecComponent } from '@/lib/mapspec-compiler/types';

function _p(partial: Partial<LayoutParticipant> & { id: string; type: string }): LayoutParticipant {
  return {
    anchor: 'top-left', floating: false, origin: 'auto',
    ...partial,
  } as LayoutParticipant;
}

// ── P1：策略链 ───────────────────────────────────────────────────────────

describe('planCompositionRepairs', () => {
  it('无碰撞 → 零动作零改动', () => {
    const plan = planCompositionRepairs({
      participants: [
        _p({ id: 't', type: 'title', anchor: 'top-center' }),
        _p({ id: 'n', type: 'north_arrow', anchor: 'top-right' }),
      ],
      canvas: { width: 1280, height: 720 },
    });
    expect(plan.steps).toEqual([]);
    expect(plan.anchorOverrides.size).toBe(0);
    expect(plan.collapseIds.size).toBe(0);
    expect(plan.hideIds.size).toBe(0);
  });

  it('槽位容量冲突 → change_anchor（跨槽兜底，确定性）', () => {
    const participants = [
      _p({ id: 'a', type: 'chart_panel', anchor: 'top-left' }),
      _p({ id: 'b', type: 'chart_panel', anchor: 'top-left' }),
      _p({ id: 'c', type: 'chart_panel', anchor: 'top-left' }),
      _p({ id: 'd', type: 'chart_panel', anchor: 'top-left' }),
      _p({ id: 'e', type: 'chart_panel', anchor: 'top-left' }),
      _p({ id: 'f', type: 'chart_panel', anchor: 'top-left' }),
    ];
    const plan = planCompositionRepairs({ participants, canvas: { width: 1280, height: 720 } });
    expect(plan.steps.length).toBeGreaterThan(0);
    const anchors = [...plan.anchorOverrides.values()];
    expect(anchors.length).toBeGreaterThan(0);
    // 全部改派到六合法槽位
    for (const zone of anchors) {
      expect(['top-left', 'top-center', 'top-right', 'bottom-left', 'bottom-center', 'bottom-right']).toContain(zone);
    }
  });

  it('user-pinned 组件绝不被挪动/折叠/隐藏（user wins）', () => {
    const participants = [
      _p({ id: 'u1', type: 'chart_panel', anchor: 'top-left', origin: 'user', floating: true, rect: { x: 10, y: 10, width: 300, height: 200 } }),
      _p({ id: 'u2', type: 'chart_panel', anchor: 'top-left', origin: 'user', floating: true, rect: { x: 20, y: 20, width: 300, height: 200 } }),
    ];
    const plan = planCompositionRepairs({ participants, canvas: { width: 1280, height: 720 } });
    expect(plan.anchorOverrides.size).toBe(0);
    expect(plan.hideIds.size).toBe(0);
    // floating×floating 只披露：user 面板不可折叠 —— 不产生动作
    expect(plan.steps).toEqual([]);
  });

  it('修复轨迹可转为 CompositionDecision（中间层 decisions 段）', () => {
    const steps = [
      { action: 'change_anchor' as const, componentId: 'x', componentType: 'legend', from: 'top-left', to: 'top-center', reason: 'slot_capacity_live' },
    ];
    const decisions = repairStepsToDecisions(steps, 5);
    expect(decisions[0]).toMatchObject({ step: 5, kind: 'repair', componentId: 'x', before: 'top-left', after: 'top-center' });
    // 规划器产出缺省 planned —— 未应用前不得谎称已执行
    expect(decisions[0].status).toBe('planned');
    expect(repairStepsToDecisions(steps, 0, 'executed')[0].status).toBe('executed');
  });
});

// ── P2：required_components_for（前端镜像）───────────────────────────────

describe('requiredComponentsFor (frontend mirror)', () => {
  it('屏幕基线：title/scale_bar/north_arrow/attribution', () => {
    const plan = requiredComponentsFor('screen_16_9', {});
    expect(plan.required.map((r) => r.type)).toEqual([
      'title', 'scale_bar', 'north_arrow', 'attribution',
    ]);
    expect(plan.missing).toEqual(['data_source']);
  });

  it('数据来源未知 → attribution 占位 + advisory（禁止省略署名）', () => {
    const plan = requiredComponentsFor('a4_portrait', {});
    const attr = plan.required.find((r) => r.type === 'attribution')!;
    expect(attr.placeholderOptions?.['text']).toBe(DATA_SOURCE_PLACEHOLDER_TEXT);
    expect(plan.advisories.some((a) => a.includes('待补充'))).toBe(true);
  });

  it('print + 全量内容 → legend/graticule/inset_map 必配', () => {
    const plan = requiredComponentsFor('a4_landscape', {
      has_thematic_layer: true,
      has_projection_info: true,
      has_location_context: true,
      has_data_source: true,
    });
    const types = plan.required.map((r) => r.type);
    for (const t of ['legend', 'graticule', 'inset_map']) expect(types).toContain(t);
  });

  it('未知用途回退 screen_16_9（§0.5）', () => {
    expect(requiredComponentsFor('hologram', {}).purpose).toBe('screen_16_9');
  });

  it('autofillComponents：只为缺席类型产出 __autofill_ id', () => {
    const items = autofillComponents('screen_16_9', new Set(['title', 'attribution']), {});
    const types = items.map((i) => i.type);
    expect(types).toContain('scale_bar');
    expect(types).toContain('north_arrow');
    expect(types).not.toContain('title');
    expect(types).not.toContain('attribution');
    for (const item of items) expect(item.id).toMatch(/^__autofill_/);
  });
});

// ── P2：composeMapLayout 编排 ────────────────────────────────────────────

function _comp(partial: Partial<MapSpecComponent> & { id: string; type: string }): MapSpecComponent {
  return { enabled: true, ...partial } as unknown as MapSpecComponent;
}

describe('composeMapLayout', () => {
  it('chrome 族缺项 → __autofill_ 注入（而非 __fallback_）', () => {
    const out = composeMapLayout({
      components: [_comp({ id: 'l1', type: 'legend' })],
      spec: null,
      zoom: 10,
      centerLat: 30,
    });
    const ids = out.renderable.map((c) => c.id);
    expect(ids).toContain('__autofill_scale_bar');
    expect(ids).toContain('__autofill_north_arrow');
    expect(ids).toContain('__autofill_attribution');
    expect(ids.every((id) => !id.startsWith('__fallback_'))).toBe(true);
    // decisions 记录 autofill provenance
    const kinds = out.descriptor.decisions.map((d) => d.kind);
    expect(kinds).toContain('autofill');
    expect(kinds).not.toContain('fallback_hit');
  });

  it('attribution 占位文本进 options.text（数据来源未知 → 待补充）', () => {
    const out = composeMapLayout({
      components: [],
      spec: null,
      zoom: 10,
      centerLat: 30,
    });
    const attr = out.renderable.find((c) => c.id === '__autofill_attribution');
    const text = (attr?.options as Record<string, unknown> | undefined)?.['text'];
    expect(text).toBe(DATA_SOURCE_PLACEHOLDER_TEXT);
  });

  it('显式 enabled:false 的类型不注入（『不要指南针』语义保持）', () => {
    const out = composeMapLayout({
      components: [
        _comp({ id: 'na', type: 'north_arrow', enabled: false }),
        _comp({ id: 'lg', type: 'legend' }),
      ],
      spec: null,
      zoom: 10,
      centerLat: 30,
    });
    const types = out.renderable.map((c) => c.type);
    expect(types).not.toContain('north_arrow');
    expect(types).toContain('scale_bar');
    expect(types).toContain('legend'); // enabled 的照常渲染
  });

  it('已有完整 chrome → 零注入零决策增量', () => {
    const out = composeMapLayout({
      components: [
        _comp({ id: 't', type: 'title', options: { text: '图名' } }),
        _comp({ id: 'na', type: 'north_arrow' }),
        _comp({ id: 'sb', type: 'scale_bar' }),
        _comp({ id: 'at', type: 'attribution', options: { text: '数据来源：示例' } }),
      ],
      spec: null,
      zoom: 10,
      centerLat: 30,
    });
    expect(out.renderable).toHaveLength(4);
    expect(out.descriptor.decisions.filter((d) => d.kind === 'autofill' && d.after === 'present')).toHaveLength(0);
    expect(out.descriptor.decisions.filter((d) => d.kind === 'fallback_hit')).toHaveLength(0);
  });

  it('descriptor：version/page/chrome 段齐备且 chrome 段可计算', () => {
    const out = composeMapLayout({
      components: [],
      spec: null,
      zoom: 10,
      centerLat: 30,
      bounds: { west: 100, south: 20, east: 120, north: 50 },
      canvas: { width: 1280, height: 720 },
    });
    expect(out.descriptor.version).toBe(1);
    expect(out.descriptor.page).toEqual({ profile: 'screen_16_9', width: 1280, height: 720 });
    expect(out.descriptor.chrome.numericScale?.label).toMatch(/^1:[\d,]+$/);
    expect(out.descriptor.chrome.declination?.approximate).toBe(true);
    expect(out.descriptor.chrome.graticule?.source).toBe('adaptive');
    expect(out.descriptor.chrome.graticule?.labelFormat).toBe('deg');
    // 元素 provenance 三值
    const origins = new Set(out.descriptor.elements.map((e) => e.origin));
    expect(origins).toContain('autofill');
  });

  it('数据承载件只进 advisory 决策（不凭空造组件）', () => {
    const out = composeMapLayout({
      components: [],
      spec: null,
      zoom: 10,
      centerLat: 30,
    });
    const types = new Set(out.renderable.map((c) => c.type));
    for (const t of ['legend', 'graticule', 'inset_map', 'title']) {
      expect(types.has(t)).toBe(false);
    }
    const advisory = out.descriptor.decisions.find(
      (d) => d.kind === 'autofill' && d.after === 'advisory_only',
    );
    expect(advisory).toBeTruthy();
  });

  it('AUTOINJECTABLE_TYPES 限定 chrome 族', () => {
    expect(AUTOINJECTABLE_TYPES.has('scale_bar')).toBe(true);
    expect(AUTOINJECTABLE_TYPES.has('north_arrow')).toBe(true);
    expect(AUTOINJECTABLE_TYPES.has('attribution')).toBe(true);
    expect(AUTOINJECTABLE_TYPES.has('legend')).toBe(false);
    expect(AUTOINJECTABLE_TYPES.has('title')).toBe(false);
  });

  it('修复决策只作 planned 记录 —— 渲染面不被擅自改（诚实审计边界）', () => {
    // 6 个 chart_panel 挤同一槽 → 规划器产出 change_anchor/collapse 等动作；
    // live 合成不得把 anchorOverrides/hideIds 应用到 renderable。
    const panels = ['a', 'b', 'c', 'd', 'e', 'f'].map((id) =>
      _comp({ id, type: 'chart_panel', position: 'top-left' }));
    const out = composeMapLayout({
      components: panels,
      spec: null,
      zoom: 10,
      centerLat: 30,
      canvas: { width: 1280, height: 720 },
    });
    // 规划器确实产出了动作（否则本测试无意义）
    const repairDecisions = out.descriptor.decisions.filter((d) => d.kind === 'repair');
    expect(repairDecisions.length).toBeGreaterThan(0);
    // 但全部标记 planned（审计工件不声称已执行）
    for (const d of repairDecisions) expect(d.status).toBe('planned');
    // 渲染面原样：组件一个不少、锚点未被改派
    const chartPanels = out.renderable.filter((c) => c.type === 'chart_panel');
    expect(chartPanels).toHaveLength(6);
    for (const c of chartPanels) {
      expect((c as unknown as { position?: string }).position).toBe('top-left');
    }
    // 元素上的修复轨迹与 decisions 同源（planned 轨迹，非已执行）
    const plannedIds = new Set(repairDecisions.map((d) => d.componentId));
    const withRepairs = out.descriptor.elements.filter((e) => e.repairs.length > 0);
    for (const e of withRepairs) expect(plannedIds.has(e.id)).toBe(true);
    expect(withRepairs.length).toBeGreaterThan(0);
  });
});

// ── P7：legend-labels v2 消费 ────────────────────────────────────────────

const V2_GRADUATED = {
  type: 'graduated',
  field: 'population',
  breaks: [0, 10, 20, 30],
  palette: 'viridis',
  palette_colors: ['#a', '#b', '#c'],
  unit: '万人',
  method: 'natural_breaks',
  k: 3,
  nodata: { color: '#ccc', label: 'legacy 无数据' },
  nodata_label: '无数据（v2）',
  out_of_range_label: '超出分级范围',
} as unknown as Parameters<typeof legendUnitSuffix>[0];

describe('legend-labels (legend_spec v2 consumption)', () => {
  it('unit 尾注：v2 在场渲染，缺省为空', () => {
    expect(legendUnitSuffix(V2_GRADUATED)).toBe('单位：万人');
    expect(legendUnitSuffix(undefined)).toBe('');
  });

  it('nodata 标签优先级：nodata_label > nodata.label > 「无数据」', () => {
    expect(legendNodataLabel(V2_GRADUATED)).toBe('无数据（v2）');
    expect(legendNodataLabel({
      type: 'graduated', breaks: [0, 1], palette: 'x', palette_colors: ['#a'],
      nodata: { color: '#ccc', label: 'legacy 无数据' },
    } as unknown as Parameters<typeof legendNodataLabel>[0])).toBe('legacy 无数据');
    expect(legendNodataLabel(undefined)).toBe(DEFAULT_NODATA_LABEL);
  });

  it('out_of_range：v1 缺省不渲染（空串），v2 在场返回标签', () => {
    expect(legendOutOfRangeLabel(undefined)).toBe('');
    expect(legendOutOfRangeLabel(V2_GRADUATED)).toBe('超出分级范围');
  });

  it('k 类目数：显式 k > breaks 推断 > categories', () => {
    expect(legendClassCount(V2_GRADUATED)).toBe(3);
    const byBreaks = legendClassCount({
      type: 'graduated', breaks: [0, 1, 2, 3], palette: 'x', palette_colors: ['#a', '#b', '#c'],
    } as unknown as Parameters<typeof legendClassCount>[0]);
    expect(byBreaks).toBe(3);
  });

  it('method 标签披露', () => {
    expect(legendMethodLabel(V2_GRADUATED)).toBe('natural_breaks');
    expect(legendMethodLabel(undefined)).toBe('');
  });
});
