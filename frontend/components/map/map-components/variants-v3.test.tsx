import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { render } from '@testing-library/react';
import { renderComponent } from './index';
import type { MapSpec, MapSpecComponent } from '@/lib/mapspec-compiler/types';

/**
 * V3（ADR-0101 D3）— 组件变体库 live 渲染契约：
 * 每个新 native 变体在 live 渲染器上有确定性视觉表达（data-variant /
 * 布局类），未知变体确定性回退默认（不崩 chrome）。与后端
 * descriptor.variants / component templates 同词表（registry 三方对账由
 * 后端测试与 catalog parity 测试锁定）。
 */

function makeSpec(): MapSpec {
  return {
    version: '1.0',
    sources: {},
    layers: [],
  } as unknown as MapSpec;
}

function ctx() {
  return { spec: makeSpec(), zoom: 10, centerLat: 30, bearing: 0 };
}

function comp(type: MapSpecComponent['type'], options: Record<string, unknown> = {}): MapSpecComponent {
  return { id: `v3-${type}`, type, enabled: true, options } as MapSpecComponent;
}

describe('V3 component variants — live rendering contract', () => {
  beforeEach(() => {
    vi.spyOn(console, 'warn').mockImplementation(() => {});
    vi.spyOn(console, 'error').mockImplementation(() => {});
  });
  afterEach(() => {
    vi.restoreAllMocks();
  });

  it('title: minimal / government variants carry data-variant + distinct typography', () => {
    const { container: minimal } = render(renderComponent(
      comp('title', { text: '成都分析图', variant: 'minimal' }), ctx(),
    ));
    const m = minimal.querySelector('[data-testid="spec-chrome-title"]');
    expect(m?.getAttribute('data-variant')).toBe('minimal');

    const { container: gov } = render(renderComponent(
      comp('title', { text: '成都市国土空间规划专题图', variant: 'government' }), ctx(),
    ));
    const g = gov.querySelector('[data-testid="spec-chrome-title"]');
    expect(g?.getAttribute('data-variant')).toBe('government');
    expect(g?.className).toContain('tracking-widest');
  });

  it('title: unknown variant deterministically falls back to academic styling', () => {
    const { container } = render(renderComponent(
      comp('title', { text: '回退标题', variant: 'mystery' }), ctx(),
    ));
    const t = container.querySelector('[data-testid="spec-chrome-title"]');
    expect(t?.getAttribute('data-variant')).toBe('mystery');
    expect(t?.className).toContain('text-title');
    expect(t?.className).toContain('font-semibold');
  });

  it('scale_bar: dual_unit renders secondary imperial row', () => {
    const { container } = render(renderComponent(
      comp('scale_bar', { variant: 'dual_unit' }), ctx(),
    ));
    const bar = container.querySelector('[data-testid="spec-chrome-scale-bar"]');
    expect(bar?.getAttribute('data-variant')).toBe('dual_unit');
    expect(bar?.textContent).toMatch(/km|m/);
    expect(bar?.textContent).toMatch(/mi|ft/);
  });

  it('north_arrow: monochrome applies grayscale presentation', () => {
    const { container } = render(renderComponent(
      comp('north_arrow', { variant: 'monochrome' }), ctx(),
    ));
    const arrow = container.querySelector('[data-testid="spec-chrome-north-arrow"]');
    expect(arrow?.getAttribute('data-variant')).toBe('monochrome');
    expect(arrow?.className).toContain('grayscale');
  });

  it('map_border: neatline renders double frame (solid + dashed)', () => {
    const { container } = render(renderComponent(
      comp('map_border', { variant: 'neatline' }), ctx(),
    ));
    const frames = container.querySelectorAll('[data-testid="spec-chrome-map-border"] > div');
    expect(frames.length).toBe(2);
    expect((frames[1] as HTMLElement).style.borderStyle).toBe('dashed');
  });

  it('statistics_panel: kpi variant renders grid of large-value cells', () => {
    const { container } = render(renderComponent(
      comp('statistics_panel', {
        variant: 'kpi',
        stats: {
          title: '执行摘要',
          items: [
            { label: '覆盖人口', value: 1234, unit: '万人', emphasis: true },
            { label: '服务设施', value: 56, unit: '处' },
          ],
        },
      }), ctx(),
    ));
    const panel = container.querySelector('[data-testid="spec-chrome-statistics-panel"]');
    expect(panel?.getAttribute('data-variant')).toBe('kpi');
    expect(panel?.textContent).toContain('覆盖人口');
    expect(panel?.textContent).toContain('1234');
  });

  it('colorbar: stepped renders discrete swatches, scientific renders intermediate ticks', () => {
    const specWithRamp: MapSpec = {
      version: '1.0',
      sources: {},
      layers: [
        {
          id: 'heat-layer',
          source: 'heat',
          type: 'circle',
          legend_spec: {
            type: 'continuous',
            field: 'density',
            min: 0,
            max: 100,
            palette: 'heat',
            palette_colors: ['#004-', '#aaa'],
          },
        } as unknown as MapSpec['layers'][number],
      ],
      // stepped/scientific 都依赖 legend_spec
    } as unknown as MapSpec;
    const rampCtx = { spec: specWithRamp, zoom: 10, centerLat: 30, bearing: 0 };
    const colors = ['#111111', '#555555', '#999999', '#eeeeee'];
    (specWithRamp.layers[0] as unknown as { legend_spec: { palette_colors: string[] } }).legend_spec.palette_colors = colors;

    const { container: stepped } = render(renderComponent(
      comp('continuous_colorbar', { variant: 'stepped', layerId: 'heat-layer' }), rampCtx,
    ));
    const swatches = stepped.querySelectorAll('[data-stepped="true"] > span');
    expect(swatches.length).toBe(colors.length);

    const { container: scientific } = render(renderComponent(
      comp('continuous_colorbar', { variant: 'scientific', layerId: 'heat-layer' }), rampCtx,
    ));
    const sci = scientific.querySelector('[data-testid="spec-chrome-colorbar"]');
    expect(sci?.getAttribute('aria-label')).toContain('科学刻度');
    // 3 个内插刻度：25/50/75% → 25 / 50 / 75
    expect(sci?.textContent).toContain('25');
    expect(sci?.textContent).toContain('50');
    expect(sci?.textContent).toContain('75');
  });
});
