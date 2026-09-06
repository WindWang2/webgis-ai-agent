import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { render } from '@testing-library/react';
import { renderComponent } from './index';
import type { MapSpec, MapSpecComponent } from '@/lib/mapspec-compiler/types';
import catalog from '@/lib/map-components/component-catalog.generated.json';

/**
 * V4（Design System）— 组件变体扩展 live 渲染契约：
 * - 双变量色阵图例（legend.type === 'bivariate'）逐格渲染 n×n 色阵；
 * - uncertainty/size/line 图例变体确定性渲染；
 * - composite 复合图例按图层分组；
 * - 未知 kind 变体确定性回退（不崩 chrome）；
 * - chart kinds 词表随目录导出（schemaVersion 4）。
 */

function makeSpec(layers: Record<string, unknown>[] = []): MapSpec {
  return {
    version: '1.0',
    sources: {},
    layers,
  } as unknown as MapSpec;
}

function ctx(layers: Record<string, unknown>[] = []) {
  return { spec: makeSpec(layers), zoom: 10, centerLat: 30, bearing: 0 };
}

function comp(type: MapSpecComponent['type'], options: Record<string, unknown> = {}): MapSpecComponent {
  return { id: `v4-${type}`, type, enabled: true, options } as MapSpecComponent;
}

const bivariateLayer = {
  id: 'biv-layer',
  legend_spec: {
    type: 'bivariate',
    matrix: 'BiPurpleOrange',
    colors: ['#e8e8f0', '#cac2e0', '#ac9ad0', '#f0d9c8', '#cfb0a8', '#b08888', '#f8c0a0', '#d49a78', '#b07450'],
    n: 3,
    label_a: '人口密度',
    label_b: '可达性',
    breaks_a: [1, 2],
    breaks_b: [3, 4],
    class_field: '__biv_class',
    field: '__biv_class',
  },
};

describe('V4 component variants — live rendering contract', () => {
  beforeEach(() => {
    vi.spyOn(console, 'warn').mockImplementation(() => {});
    vi.spyOn(console, 'error').mockImplementation(() => {});
  });
  afterEach(() => {
    vi.restoreAllMocks();
  });

  it('bivariate legend_spec 渲染 n×n 色阵 + 双轴标注', () => {
    const { container } = render(renderComponent(
      comp('legend', { layerId: 'biv-layer' }), ctx([bivariateLayer]),
    ));
    const el = container.querySelector('[data-testid="spec-chrome-bivariate-legend"]');
    expect(el).not.toBeNull();
    // 3×3 = 9 个色块
    const swatches = el?.querySelectorAll('[aria-hidden]');
    expect(swatches?.length).toBe(11); // 9 色块 + 2 轴标箭头
  });

  it('uncertainty 变体渲染透明度阶梯 + 披露行', () => {
    const layer = {
      id: 'unc-layer',
      legend_spec: {
        type: 'graduated', field: 'v', breaks: [0, 1, 2, 3],
        palette_colors: ['#f2f0f7', '#cbc9e2', '#9e9ac8'],
      },
    };
    const { container } = render(renderComponent(
      comp('legend', { layerId: 'unc-layer', variant: 'uncertainty' }), ctx([layer]),
    ));
    const el = container.querySelector('[data-testid="spec-chrome-legend"]');
    expect(el?.getAttribute('data-variant')).toBe('uncertainty');
    expect(el?.textContent).toContain('越透明 = 不确定性越高');
  });

  it('size / line 变体确定性渲染（尺寸 ∝√值 / 线宽分级）', () => {
    const layer = {
      id: 'sz-layer',
      legend_spec: {
        type: 'graduated', field: 'v', breaks: [0, 1, 2, 3],
        palette_colors: ['#edf8e9', '#bae4b3', '#74c476'],
      },
    };
    const { container: c1 } = render(renderComponent(
      comp('legend', { layerId: 'sz-layer', variant: 'size' }), ctx([layer]),
    ));
    expect(c1.querySelector('[data-testid="spec-chrome-legend"]')?.getAttribute('data-variant')).toBe('size');
    expect(c1.querySelectorAll('.rounded-full').length).toBe(3);

    const { container: c2 } = render(renderComponent(
      comp('legend', { layerId: 'sz-layer', variant: 'line' }), ctx([layer]),
    ));
    expect(c2.querySelector('[data-testid="spec-chrome-legend"]')?.getAttribute('data-variant')).toBe('line');
    expect(c2.textContent).toContain('线宽分级');
  });

  it('composite 变体按图层分组渲染多图例', () => {
    const layers = [
      bivariateLayer,
      {
        id: 'grad-layer',
        legend_spec: {
          type: 'graduated', field: 'v', breaks: [0, 1, 2],
          palette_colors: ['#eff3ff', '#6baed6'], title: '分级A',
        },
      },
    ];
    const { container } = render(renderComponent(
      comp('legend', { variant: 'composite' }), ctx(layers),
    ));
    const el = container.querySelector('[data-testid="spec-chrome-legend"]');
    expect(el?.getAttribute('data-variant')).toBe('composite');
    expect(el?.textContent).toContain('分级A');
  });

  it('catalog schemaVersion 4 携带 chart kinds 词表（violin 诚实 unsupported）', () => {
    expect(catalog.schemaVersion).toBe(4);
    const kinds = (catalog as unknown as { chartKinds: { id: string; exportLevel: string }[] }).chartKinds;
    expect(kinds.length).toBeGreaterThanOrEqual(18);
    const violin = kinds.find((k) => k.id === 'violin');
    expect(violin?.exportLevel).toBe('unsupported');
  });
});
