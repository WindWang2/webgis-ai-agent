import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { render, screen } from '@testing-library/react';
import { renderComponent } from './index';
import type { MapSpec, MapSpecComponent } from '@/lib/mapspec-compiler/types';

/**
 * #998 ② — MapSpec 色条（continuous_colorbar）两端刻度走统一
 * formatLegendValue 并渲染 unit。旧实现固定 Number(x).toFixed(1)：0–0.004
 * 的密度区间两端都印成 0.0（完全失真），且丢弃 legend_spec.unit。
 */
function makeSpec(legendSpec: Record<string, unknown>): MapSpec {
  return {
    version: '1.0',
    sources: { heat: { type: 'geojson', url: '/heat.json' } },
    layers: [
      {
        id: 'heat-layer',
        source: 'heat',
        type: 'circle',
        legend_spec: legendSpec,
      } as unknown as MapSpec['layers'][number],
    ],
  } as MapSpec;
}

function makeComponent(): MapSpecComponent {
  return { id: 'cb-1', type: 'continuous_colorbar', enabled: true };
}

function renderColorbar(legendSpec: Record<string, unknown>) {
  return render(
    <>
      {renderComponent(makeComponent(), {
        spec: makeSpec(legendSpec),
        zoom: 10,
        centerLat: 30,
        bearing: 0,
      })}
    </>,
  );
}

describe('Colorbar renderer — shared formatter + unit (#998)', () => {
  beforeEach(() => {
    vi.spyOn(console, 'warn').mockImplementation(() => {});
    vi.spyOn(console, 'error').mockImplementation(() => {});
  });
  afterEach(() => {
    vi.restoreAllMocks();
  });

  it('formats both ticks with formatLegendValue (0–0.004 no longer collapses to 0.0/0.0)', () => {
    renderColorbar({
      type: 'continuous',
      field: 'density',
      min: 0,
      max: 0.004,
      palette: 'heat',
      palette_colors: ['#0ff0ff', '#ff2d55'],
      unit: '人/km²',
    });
    expect(screen.getByText('0')).toBeInTheDocument();
    expect(screen.getByText('0.004')).toBeInTheDocument();
    expect(screen.queryByText('0.0')).toBeNull();
    // unit 渲染（此前被丢弃）
    expect(screen.getByText('人/km²')).toBeInTheDocument();
  });

  it('renders large magnitudes compactly, consistent with the thematic legends', () => {
    renderColorbar({
      type: 'continuous',
      min: 0,
      max: 2_500_000,
      palette: 'heat',
      palette_colors: ['#0ff0ff', '#ff2d55'],
    });
    // 2.5M（千分位/M-k 压缩与 legend-card 同款），不是 "2500000.0"
    expect(screen.getByText('2.5M')).toBeInTheDocument();
    expect(screen.queryByText('2500000.0')).toBeNull();
  });

  it('renders without the unit slot when legend_spec has no unit', () => {
    renderColorbar({
      type: 'divergent',
      center: 0,
      min: -12.5,
      max: 12.5,
      palette: 'rdylbu',
      palette_colors: ['#1a9850', '#ffffbf', '#d73027'],
    });
    expect(screen.getByText('-12.5')).toBeInTheDocument();
    expect(screen.getByText('12.5')).toBeInTheDocument();
  });

  it('renders the bare gradient when min/max are absent (no range row)', () => {
    const { container } = render(
      <>
        {renderComponent(makeComponent(), {
          spec: makeSpec({
            type: 'continuous',
            palette: 'heat',
            palette_colors: ['#0ff0ff', '#ff2d55'],
          }),
          zoom: 10,
          centerLat: 30,
          bearing: 0,
        })}
      </>,
    );
    const bar = container.querySelector('[data-testid="spec-chrome-colorbar"]');
    expect(bar).toBeTruthy();
    expect(screen.queryByText('0')).toBeNull();
  });

  it('显式 layerId 未命中/无 legend_spec 时不串用别层图例', () => {
    const spec = {
      version: '1.0',
      sources: {},
      layers: [
        { id: 'target-layer', source: 's', type: 'circle' },
        {
          id: 'other-layer', source: 's', type: 'circle',
          legend_spec: {
            type: 'continuous', min: 0, max: 99,
            palette_colors: ['#0ff0ff', '#ff2d55'],
          },
        },
      ],
    } as unknown as MapSpec;
    render(
      <>
        {renderComponent(
          { id: 'cb-explicit', type: 'continuous_colorbar', enabled: true, options: { layerId: 'target-layer' } },
          { spec, zoom: 10, centerLat: 30, bearing: 0 },
        )}
      </>,
    );
    // 已声明 layerId 却无图例 → 不渲染；绝不回退展示 other-layer 的 0–99。
    expect(screen.queryByTestId('spec-chrome-colorbar')).toBeNull();
    expect(screen.queryByText('99')).toBeNull();
  });
});
