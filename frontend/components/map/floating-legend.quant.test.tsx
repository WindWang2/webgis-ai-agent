import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen } from '@testing-library/react';
import { FloatingLegend } from './floating-legend';
import type { Layer } from '@/lib/types/layer';

/**
 * #998 ① — 热力图例的量化刻度。legend_spec（continuous/divergent）携带的
 * min/max/unit 此前被整段丢弃，只剩『极高』这类定性形容词；带 min/max 时
 * 必须渲染 formatLegendValue 数值行（与专题图例 / 色条同源），定性标签只
 * 作无 spec 的兜底。
 */
const storeState = vi.hoisted(() => ({
  layers: [] as Layer[],
}));

vi.mock('@/lib/store/useHudStore', () => ({
  useHudStore: (selector: (s: any) => any) => selector(storeState),
}));

function heatmapLayer(legend_spec: Layer['legend_spec']): Layer {
  return {
    id: 'ref:heat-1',
    name: '热力图分析',
    type: 'heatmap',
    visible: true,
    opacity: 1,
    legend_spec,
  } as Layer;
}

describe('FloatingLegend — quantized scale (#998)', () => {
  beforeEach(() => {
    storeState.layers = [];
  });

  it('renders formatLegendValue min/max + unit for a continuous spec (0–0.004 density)', () => {
    storeState.layers = [
      heatmapLayer({
        type: 'continuous',
        field: 'density',
        min: 0,
        max: 0.004,
        palette: 'heat',
        palette_colors: ['#0ff0ff', '#00ff41', '#ffff00', '#ff5f00', '#ff2d55'],
        unit: '人/km²',
      }),
    ];
    const { container } = render(<FloatingLegend />);

    // 两端刻度走统一格式化器：0 整数直印，0.004 保留 3 位小数
    // （旧实现的定性标签『极低/极高』完全丢失量级）。
    expect(screen.getByText('0')).toBeInTheDocument();
    expect(screen.getByText('0.004')).toBeInTheDocument();
    expect(screen.getByText('人/km²')).toBeInTheDocument();
    // 定性标签在带量化 spec 时不再渲染
    expect(screen.queryByText('极低')).toBeNull();
    expect(screen.queryByText('极高')).toBeNull();
    // 色带仍来自 palette_colors（单一色源；jsdom 将 #0ff0ff 归一为 rgb）
    const swatch = container.querySelector('div.h-2 > div');
    expect(swatch).toBeTruthy();
    expect(swatch!.getAttribute('style')).toContain('rgb(15, 240, 255)');
  });

  it('renders the numeric row for a divergent spec too', () => {
    storeState.layers = [
      heatmapLayer({
        type: 'divergent',
        field: 'z_score',
        center: 0,
        min: -3.5,
        max: 3.5,
        palette: 'rdylbu',
        palette_colors: ['#1a9850', '#ffffbf', '#d73027'],
      }),
    ];
    render(<FloatingLegend />);
    expect(screen.getByText('-3.5')).toBeInTheDocument();
    expect(screen.getByText('3.5')).toBeInTheDocument();
    expect(screen.queryByText('极低')).toBeNull();
  });

  it('falls back to qualitative labels when the layer has no legend_spec', () => {
    storeState.layers = [heatmapLayer(undefined)];
    render(<FloatingLegend />);
    expect(screen.getByText('极低')).toBeInTheDocument();
    expect(screen.getByText('极高')).toBeInTheDocument();
    expect(screen.queryByText('人/km²')).toBeNull();
  });

  it('falls back to qualitative labels when min/max are malformed (defensive)', () => {
    storeState.layers = [
      heatmapLayer({
        type: 'continuous',
        min: Number.NaN,
        max: 1,
        palette: 'heat',
        palette_colors: ['#0ff0ff', '#ff2d55'],
      } as unknown as Layer['legend_spec']),
    ];
    render(<FloatingLegend />);
    expect(screen.getByText('极低')).toBeInTheDocument();
  });
});
