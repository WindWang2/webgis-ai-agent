import { render } from '@testing-library/react';
import { describe, it, expect, afterEach } from 'vitest';
import { FloatingLegend } from './floating-legend';
import { useHudStore } from '@/lib/store/useHudStore';
import type { Layer } from '@/lib/types/layer';

function heatLayer(overrides: Partial<Layer> = {}): Layer {
  return {
    id: 'h1',
    name: '学校密度',
    type: 'heatmap',
    visible: true,
    opacity: 1,
    ...overrides,
  };
}

function setLayers(layers: Layer[]) {
  useHudStore.setState({ layers });
}

afterEach(() => {
  useHudStore.setState({ layers: [] });
});

describe('FloatingLegend — #679 单一色源', () => {
  // jsdom 把内联 hex 色规范化为 rgb(...) —— 按 backgroundColor 值断言
  const swatchColors = (container: HTMLElement) =>
    Array.from(container.querySelectorAll('div[style]'))
      .map((d) => (d as HTMLElement).style.backgroundColor)
      .filter((c) => c !== '' && c !== 'none');

  it('swatch colors derive from the heatmap layer legend_spec.palette_colors', () => {
    setLayers([heatLayer({
      legend_spec: {
        type: 'continuous',
        min: 0,
        max: 1,
        palette: 'YlOrRd',
        palette_colors: ['#428cd2', '#3dbce8', '#60d678', '#fae032', '#fa8c28', '#eb2828'],
      },
    })]);
    const { container } = render(<FloatingLegend />);
    const colors = swatchColors(container);
    // 后端 classic 调色板可见段
    expect(colors).toContain('rgb(66, 140, 210)');
    expect(colors).toContain('rgb(235, 40, 40)');
    // 旧硬编码 cyan→red 不再出现（图例与地图同色源）
    expect(colors).not.toContain('rgb(0, 240, 255)');
    expect(colors).not.toContain('rgb(255, 45, 85)');
  });

  it('falls back to the legacy fixed band when the layer carries no legend_spec', () => {
    setLayers([heatLayer()]);
    const { container } = render(<FloatingLegend />);
    const colors = swatchColors(container);
    expect(colors).toContain('rgb(15, 240, 255)');
    expect(colors).toContain('rgb(255, 45, 85)');
  });

  it('hidden (no visible heatmap layer) is aria-hidden and faded out', () => {
    setLayers([heatLayer({ visible: false })]);
    const { container } = render(<FloatingLegend />);
    const wrapper = container.firstChild as HTMLElement;
    expect(wrapper).toHaveAttribute('aria-hidden', 'true');
    // 隐藏是 opacity 0 淡出，不是卸载（与组件既有过渡语义一致）
    expect(wrapper.style.opacity).toBe('0');
  });
});
