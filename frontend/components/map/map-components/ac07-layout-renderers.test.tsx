/**
 * AC-07（ADR-0156）渲染器级验收：数字比例尺 / 磁偏角注记 / 经纬网密度
 * 与图廓注记 / 图例 v2 字段族 / inset_map source 隔离。
 */
import { describe, expect, it } from 'vitest';
import { render, screen } from '@testing-library/react';
import { readFileSync } from 'node:fs';
import { join } from 'node:path';

import '../map-components';
import { renderComponent } from '../map-components';
import type { MapSpecComponent } from '@/lib/mapspec-compiler/types';
import type { RendererContext } from '../map-components/types';

const BOUNDS = { west: 100, south: 20, east: 120, north: 50 };

function baseCtx(partial: Partial<RendererContext> = {}): RendererContext {
  return {
    spec: null,
    zoom: 10,
    centerLat: 30,
    bearing: 0,
    ...partial,
  };
}

function comp(partial: Partial<MapSpecComponent> & { id: string; type: string }): MapSpecComponent {
  return { enabled: true, ...partial } as unknown as MapSpecComponent;
}

// ── P3：数字比例尺与图形条并存 ───────────────────────────────────────────

describe('scale_bar numeric ratio (P3)', () => {
  it('默认并存：图形条 + 1:N 数字比例尺', () => {
    render(renderComponent(comp({ id: 'sb', type: 'scale_bar' }), baseCtx()));
    expect(screen.getByTestId('spec-chrome-scale-bar')).toBeTruthy();
    const numeric = screen.getByTestId('spec-chrome-scale-numeric');
    expect(numeric.textContent).toMatch(/^1:[\d,]+$/);
  });

  it('numeric 模式：只渲染数字比例尺', () => {
    render(renderComponent(
      comp({ id: 'sb', type: 'scale_bar', options: { scaleDisplay: 'numeric' } }),
      baseCtx(),
    ));
    expect(screen.getByTestId('spec-chrome-scale-numeric')).toBeTruthy();
    expect(screen.getByTestId('spec-chrome-scale-bar').getAttribute('data-scale-display')).toBe('numeric');
  });

  it('纬度修正：同一 zoom 下高纬中心比低纬分母小（aria 标签随 centerLat 变化）', () => {
    const first = render(renderComponent(
      comp({ id: 'sb', type: 'scale_bar', options: { scaleDisplay: 'numeric' } }),
      baseCtx({ centerLat: 0 }),
    ));
    const labelEquator = screen.getByTestId('spec-chrome-scale-numeric').textContent;
    first.unmount();
    const second = render(renderComponent(
      comp({ id: 'sb', type: 'scale_bar', options: { scaleDisplay: 'numeric' } }),
      baseCtx({ centerLat: 70 }),
    ));
    const labelHigh = screen.getByTestId('spec-chrome-scale-numeric').textContent;
    second.unmount();
    expect(labelEquator).not.toBe(labelHigh);
  });
});

// ── P4：真北/磁北偏角注记 ────────────────────────────────────────────────

describe('north_arrow declination (P4)', () => {
  it('bounds 在场 → 偏角注记（approximate 标记）', () => {
    render(renderComponent(comp({ id: 'na', type: 'north_arrow' }), baseCtx({ bounds: BOUNDS })));
    const decl = screen.getByTestId('spec-chrome-north-declination');
    expect(decl.textContent).toMatch(/≈[\d.]+°[EW] \(approximate\)/);
  });

  it('bounds 缺席 → 不注记（不虚构）', () => {
    render(renderComponent(comp({ id: 'na', type: 'north_arrow' }), baseCtx()));
    expect(screen.queryByTestId('spec-chrome-north-declination')).toBeNull();
  });

  it('options.showDeclination === false → 可关', () => {
    render(renderComponent(
      comp({ id: 'na', type: 'north_arrow', options: { showDeclination: false } }),
      baseCtx({ bounds: BOUNDS }),
    ));
    expect(screen.queryByTestId('spec-chrome-north-declination')).toBeNull();
  });
});

// ── P4/P5：经纬网密度自适应与图廓注记 ────────────────────────────────────

describe('graticule density & frame labels (P4/P5)', () => {
  it('密度自适应：data 属性披露来源与线数（[3,10]）', () => {
    const { container } = render(renderComponent(
      comp({ id: 'g', type: 'graticule' }),
      baseCtx({ bounds: BOUNDS, zoom: 6 }),
    ));
    const root = container.querySelector('[data-testid="spec-chrome-graticule"]')!;
    expect(root.getAttribute('data-density-source')).toBe('adaptive');
    const lng = Number(root.getAttribute('data-line-count-lng'));
    const lat = Number(root.getAttribute('data-line-count-lat'));
    expect(lng).toBeGreaterThanOrEqual(3);
    expect(lng).toBeLessThanOrEqual(10);
    expect(lat).toBeGreaterThanOrEqual(3);
    expect(lat).toBeLessThanOrEqual(10);
  });

  it('显式 interval 覆盖优先', () => {
    const { container } = render(renderComponent(
      comp({ id: 'g', type: 'graticule', options: { interval: 5 } }),
      baseCtx({ bounds: BOUNDS, zoom: 6 }),
    ));
    const root = container.querySelector('[data-testid="spec-chrome-graticule"]')!;
    expect(root.getAttribute('data-density-source')).toBe('explicit');
    // 20° 跨度 / 5° 间隔 = 5 条经度线
    expect(Number(root.getAttribute('data-line-count-lng'))).toBe(5);
  });

  it('图廓四角注记渲染（8 个角标 span = 4 角 × 经纬）', () => {
    const { container } = render(renderComponent(
      comp({ id: 'g', type: 'graticule' }),
      baseCtx({ bounds: BOUNDS, zoom: 6 }),
    ));
    const root = container.querySelector('[data-testid="spec-chrome-graticule"]')!;
    // 角注记带 vertical-rl 写作模式的纬度标签 ×4
    const vertical = root.querySelectorAll('span[style*="vertical-rl"]');
    expect(vertical.length).toBe(4);
  });

  it('showFrameLabels=false 可关', () => {
    const { container } = render(renderComponent(
      comp({ id: 'g', type: 'graticule', options: { showFrameLabels: false } }),
      baseCtx({ bounds: BOUNDS, zoom: 6 }),
    ));
    const root = container.querySelector('[data-testid="spec-chrome-graticule"]')!;
    expect(root.querySelectorAll('span[style*="vertical-rl"]').length).toBe(0);
  });
});

// ── P7：图例 v2 字段族（fixture 驱动）────────────────────────────────────

const SPEC_WITH_V2_LEGEND = {
  layers: [
    {
      id: 'layer-primary',
      type: 'fill',
      paint: {},
      legend_spec: {
        type: 'graduated',
        field: 'population',
        breaks: [0, 10, 20, 30],
        palette: 'viridis',
        palette_colors: ['#111', '#222', '#333'],
        unit: '万人',
        method: 'natural_breaks',
        k: 3,
        nodata: { color: '#cccccc', label: 'legacy 无数据' },
        nodata_label: '无数据（v2）',
        out_of_range_label: '超出分级范围',
      },
    },
  ],
} as unknown as RendererContext['spec'];

describe('legend v2 fields (P7)', () => {
  it('unit 尾注 / method 披露 / nodata v2 覆写 / out_of_range 条目 / k 类目', () => {
    render(renderComponent(
      comp({ id: 'lg', type: 'legend', options: { layerId: 'layer-primary' } }),
      baseCtx({ spec: SPEC_WITH_V2_LEGEND }),
    ));
    expect(screen.getByTestId('spec-chrome-legend-unit')!.textContent).toContain('单位：万人');
    expect(screen.getByTestId('spec-chrome-legend-unit')!.textContent).toContain('共 3 类');
    expect(screen.getByTestId('spec-chrome-legend-method')!.textContent).toBe('natural_breaks');
    expect(screen.getByTestId('spec-chrome-legend-out-of-range')!.textContent).toBe('超出分级范围');
    // nodata 条目是最后一项 —— v2 nodata_label 覆写 legacy 文本
    const root = screen.getByTestId('spec-chrome-legend');
    const labels = [...root.querySelectorAll('span')].map((s) => s.textContent);
    expect(labels).toContain('无数据（v2）');
    expect(labels).not.toContain('legacy 无数据');
  });

  it('continuous 色条：nodata 色块与 out_of_range 标签', () => {
    const spec = {
      layers: [
        {
          id: 'layer-primary',
          type: 'fill',
          paint: {},
          legend_spec: {
            type: 'continuous',
            min: 0,
            max: 100,
            palette: 'viridis',
            palette_colors: ['#111', '#888', '#fff'],
            unit: '人/km²',
            nodata: { color: '#e0e0e0', label: 'nodata' },
            out_of_range_label: '超出值域',
          },
        },
      ],
    } as unknown as RendererContext['spec'];
    render(renderComponent(
      comp({ id: 'cb', type: 'continuous_colorbar', options: { layerId: 'layer-primary' } }),
      baseCtx({ spec }),
    ));
    expect(screen.getByTestId('spec-chrome-colorbar-nodata')!.textContent).toContain('nodata');
    expect(screen.getByTestId('spec-chrome-colorbar-out-of-range')!.textContent).toBe('超出值域');
  });

  it('v1 payload（无 v2 字段）干净回退：不出 out_of_range / 不报错', () => {
    const spec = {
      layers: [
        {
          id: 'layer-primary',
          type: 'fill',
          paint: {},
          legend_spec: {
            type: 'categorical',
            field: 'zone',
            categories: [
              { key: 'a', color: '#111', label: 'A' },
              { key: 'b', color: '#222', label: 'B' },
            ],
          },
        },
      ],
    } as unknown as RendererContext['spec'];
    render(renderComponent(
      comp({ id: 'cl', type: 'categorical_legend', options: { layerId: 'layer-primary' } }),
      baseCtx({ spec }),
    ));
    expect(screen.getByTestId('spec-chrome-categorical-legend')).toBeTruthy();
    expect(screen.queryByTestId('spec-chrome-legend-out-of-range')).toBeNull();
    expect(screen.getByTestId('spec-chrome-categorical-legend-unit')!.textContent).toContain('共 2 类');
  });
});

// ── P6：inset_map source 隔离 ────────────────────────────────────────────

describe('inset_map source isolation (P6)', () => {
  it('渲染器不 mount 第二个 maplibre runtime（静态源扫描锁定）', () => {
    const source = readFileSync(
      join(process.cwd(), 'components/map/map-components/inset-map.tsx'),
      'utf-8',
    );
    expect(source).not.toMatch(/from ['"]maplibre-gl/);
    expect(source).not.toMatch(/new\s+maplibre\.Map/);
    expect(source).not.toMatch(/useMap\(\)/);
  });

  it('bbox 在场即渲染；主图指示框走独立 options（不读主图数据源）', () => {
    render(renderComponent(
      comp({
        id: 'inset',
        type: 'inset_map',
        options: {
          bbox: [95, 15, 125, 40],
          mainBbox: [103, 25, 113, 35],
        },
      }),
      baseCtx(),
    ));
    expect(screen.getByTestId('spec-chrome-inset-map')).toBeTruthy();
  });

  it('bbox 缺席自弃（不虚构范围）', () => {
    render(renderComponent(comp({ id: 'inset', type: 'inset_map' }), baseCtx()));
    expect(screen.queryByTestId('spec-chrome-inset-map')).toBeNull();
  });
});
