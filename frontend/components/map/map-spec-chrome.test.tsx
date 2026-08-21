/**
 * MapSpecChrome —— MapSpec layout.components 契约渲染面测试。
 * GIS Harness（后端）写组件 → live chrome 按 enabled/position 渲染。
 */
import { describe, expect, it } from 'vitest';
import { render, screen } from '@testing-library/react';
import { MapSpecChrome } from '@/components/map/map-spec-chrome';
import type { MapSpec, MapSpecComponent } from '@/lib/mapspec-compiler/types';

function comp(partial: Partial<MapSpecComponent> & { id: string; type: MapSpecComponent['type'] }): MapSpecComponent {
  return { enabled: true, position: 'none', ...partial };
}

const baseSpec: MapSpec = {
  version: '1.0',
  sources: {},
  layers: [
    {
      id: 'product-heat',
      source: 's1',
      type: 'heatmap',
      legend_spec: {
        type: 'continuous',
        min: 0,
        max: 1,
        palette_colors: ['#428cd2', '#60d678', '#fae032', '#eb2828'],
      },
    } as MapSpec['layers'][number] & { legend_spec: unknown },
  ],
};

describe('MapSpecChrome', () => {
  it('renders title / north arrow / scale bar / attribution / colorbar', () => {
    render(
      <MapSpecChrome
        components={[
          comp({ id: 'title', type: 'title', position: 'top-center', options: { text: '成都市小学分布' } }),
          comp({ id: 'north-arrow', type: 'north_arrow', position: 'top-right', options: { variant: 'compass_minimal_black' } }),
          comp({ id: 'scale-bar', type: 'scale_bar', position: 'bottom-right' }),
          comp({ id: 'attribution', type: 'attribution', position: 'bottom-left', options: { text: '© OpenStreetMap contributors' } }),
          comp({ id: 'colorbar-main', type: 'continuous_colorbar', position: 'bottom-right' }),
        ]}
        zoom={10}
        centerLat={30.6}
        bearing={0}
        spec={baseSpec}
      />,
    );
    expect(screen.getByTestId('spec-chrome-title').textContent).toBe('成都市小学分布');
    expect(screen.getByTestId('spec-chrome-north-arrow')).toBeTruthy();
    expect(screen.getByTestId('spec-chrome-scale-bar')).toBeTruthy();
    expect(screen.getByTestId('spec-chrome-attribution').textContent)
      .toBe('© OpenStreetMap contributors');
    // 色条从 committed spec 图层的 legend_spec 取色
    const colorbar = screen.getByTestId('spec-chrome-colorbar');
    expect(colorbar.innerHTML).toContain('#428cd2');
  });

  it('respects enabled=false (『不要指南针』)', () => {
    render(
      <MapSpecChrome
        components={[
          comp({ id: 'north-arrow', type: 'north_arrow', enabled: false }),
          comp({ id: 'title', type: 'title', position: 'top-center', options: { text: 't' } }),
        ]}
        zoom={10}
        centerLat={30}
        bearing={0}
        spec={baseSpec}
      />,
    );
    expect(screen.queryByTestId('spec-chrome-north-arrow')).toBeNull();
    expect(screen.getByTestId('spec-chrome-title')).toBeTruthy();
  });

  it('renders north arrow variants (『换一个指南针』→ compass_rose)', () => {
    render(
      <MapSpecChrome
        components={[comp({ id: 'north-arrow', type: 'north_arrow', options: { variant: 'compass_rose' } })]}
        zoom={10}
        centerLat={30}
        bearing={0}
        spec={baseSpec}
      />,
    );
    expect(screen.getByTestId('spec-chrome-north-arrow').getAttribute('aria-label'))
      .toContain('compass_rose');
  });

  it('renders vertical colorbar (『色条改成竖向』)', () => {
    render(
      <MapSpecChrome
        components={[comp({ id: 'cb', type: 'continuous_colorbar', options: { orientation: 'vertical' } })]}
        zoom={10}
        centerLat={30}
        bearing={0}
        spec={baseSpec}
      />,
    );
    const cb = screen.getByTestId('spec-chrome-colorbar');
    expect(cb.innerHTML).toContain('to bottom');
  });

  it('renders graduated legend component from layer legend_spec', () => {
    const spec: MapSpec = {
      ...baseSpec,
      layers: [
        {
          id: 'district-choro',
          source: 's1',
          type: 'fill',
          legend_spec: {
            type: 'graduated',
            title: '小学数量',
            entries: [
              { color: '#ffffb2', label: '0-10' },
              { color: '#bd0026', label: '50+' },
            ],
          },
        } as unknown as MapSpec['layers'][number],
      ],
    };
    render(
      <MapSpecChrome
        components={[comp({ id: 'legend-main', type: 'legend' })]}
        zoom={10}
        centerLat={30}
        bearing={0}
        spec={spec}
      />,
    );
    expect(screen.getByTestId('spec-chrome-legend').textContent).toContain('小学数量');
    expect(screen.getByTestId('spec-chrome-legend').textContent).toContain('50+');
  });

  it('renders nothing when all components disabled', () => {
    const { container } = render(
      <MapSpecChrome
        components={[comp({ id: 'title', type: 'title', enabled: false, options: { text: 'x' } })]}
        zoom={10}
        centerLat={30}
        bearing={0}
        spec={baseSpec}
      />,
    );
    expect(container.firstChild).toBeNull();
  });
});
