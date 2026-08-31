import { describe, expect, it, beforeEach, vi } from 'vitest';
import { render, fireEvent } from '@testing-library/react';
import type { MapSpecComponent } from '@/lib/mapspec-compiler/types';

/**
 * Map ↔ Chart 共享选择（Workspace V2 / Goal D3/D4）：
 *
 * - chart→map：类别点击发布 select（layer_id + selectionField 协议），
 *   地图侧把类别选择编译为 per-layer 过滤表达式（adapter 纯函数）——
 *   同一 compose/reconcile 通道，只重编译 filter，不重建 source/layer、
 *   不重拉数据（Scenario B 契约）；
 * - map→chart：selection.source=map 且 layer 匹配时按 selectionField 从
 *   有界属性快照推导高亮类别；
 * - Selection 是 transient：任何选择变化都不得触发 MapSpec mutation
 *   （patch_component / user mutation —— MapSpec = desired product state）。
 */

const mutationMocks = vi.hoisted(() => ({
  commitComponentPatch: vi.fn(() => Promise.resolve()),
}));

vi.mock('@/lib/mapspec/component-mutation', () => ({
  ...mutationMocks,
  getComponentPlacementOverride: () => undefined,
  subscribeComponentOverrides: () => () => {},
  getComponentOverridesGeneration: () => 0,
  setComponentPlacementOverride: vi.fn(),
}));

vi.mock('recharts', async () => {
  const React = await import('react');
  const pass = ({ children }: { children?: React.ReactNode }) =>
    React.createElement('div', null, children);
  return {
    ResponsiveContainer: pass,
    BarChart: pass,
    LineChart: pass,
    PieChart: pass,
    ScatterChart: pass,
    CartesianGrid: pass,
    XAxis: pass,
    YAxis: pass,
    Tooltip: pass,
    Legend: pass,
    // 断言面：Bar 转发类别点击载荷，Cell 渲染 ChartCore 计算的 fill。
    Bar: ({ children, onClick }: { children?: React.ReactNode; onClick?: (s: unknown) => void }) =>
      React.createElement(
        'div',
        {
          className: 'recharts-bar-rectangle',
          'data-testid': 'chart-bar',
          onClick: () => onClick?.({ payload: { name: '武侯区' } }),
        },
        children,
      ),
    Line: pass,
    Pie: pass,
    Cell: ({ fill }: { fill?: string }) =>
      React.createElement('path', { className: 'recharts-cell', fill: fill ?? '' }),
    Scatter: pass,
  };
});

import { renderComponent } from '@/components/map/map-components';
import { hudStateToMapSpec } from '@/lib/mapspec-runtime/adapter';
import {
  clearSelection,
  getSelection,
  publishSelection,
  resetSelectionStore,
} from '@/lib/selection/selection-store';
import type { Layer } from '@/lib/types/layer';

const CTX = { spec: null, zoom: 10, centerLat: 30, bearing: 0 } as const;

function chartComponent(options: Record<string, unknown> = {}): MapSpecComponent {
  return {
    id: 'chart-panel',
    type: 'chart_panel',
    enabled: true,
    options: {
      layerId: 'district-choropleth',
      selectionField: 'district',
      chart: {
        title: '各区学校数量',
        type: 'bar',
        data: [
          { name: '武侯区', value: 12 },
          { name: '锦江区', value: 8 },
          { name: '高新区', value: 15 },
        ],
      },
      ...options,
    },
  } as unknown as MapSpecComponent;
}

function hudLayer(id: string): Layer {
  return {
    id,
    name: id,
    type: 'vector',
    visible: true,
    opacity: 1,
    source: {
      type: 'FeatureCollection',
      features: [
        { type: 'Feature', geometry: { type: 'Polygon', coordinates: [[[0, 0], [1, 0], [1, 1], [0, 0]]] }, properties: { district: '武侯区' } },
        { type: 'Feature', geometry: { type: 'Polygon', coordinates: [[[2, 0], [3, 0], [3, 1], [2, 0]]] }, properties: { district: '锦江区' } },
      ],
    },
  };
}

describe('chart → map selection', () => {
  beforeEach(() => {
    resetSelectionStore();
    mutationMocks.commitComponentPatch.mockClear();
  });

  it('category click publishes a chart select with the selection protocol', () => {
    render(<>{renderComponent(chartComponent(), CTX)}</>);
    // recharts Bar onClick (jsdom fires through the wrapper)
    const bars = document.querySelectorAll('[data-testid="chart-bar"]');
    expect(bars.length).toBeGreaterThan(0);
    fireEvent.click(bars[0]);
    const sel = getSelection();
    expect(sel).toMatchObject({
      source: 'chart',
      layer_id: 'district-choropleth',
      selected_categories: [expect.stringContaining('区')],
      filter_field: 'district',
    });
  });

  it('selection changes never write MapSpec (no component patch)', () => {
    render(<>{renderComponent(chartComponent(), CTX)}</>);
    const bars = document.querySelectorAll('.recharts-bar-rectangle, path.recharts-rectangle');
    fireEvent.click(bars[0]);
    clearSelection();
    expect(mutationMocks.commitComponentPatch).not.toHaveBeenCalled();
  });

  it('adapter compiles the category selection into the layer filter — sources untouched', () => {
    const layers = [hudLayer('district-choropleth')];
    const base = hudStateToMapSpec({
      layers,
      processLayers: {},
      activeFilters: {},
      is3D: false,
    });
    const filtered = hudStateToMapSpec({
      layers,
      processLayers: {},
      activeFilters: {},
      selectionFilters: {
        'district-choropleth': ['in', ['get', 'district'], '武侯区'],
      },
      is3D: false,
    });
    // 数据面零重建零重拉（Scenario B）：inlineData 载荷是同一 FC 引用。
    const baseSource = base.sources['district-choropleth'] as { inlineData?: unknown };
    const filteredSource = filtered.sources['district-choropleth'] as { inlineData?: unknown };
    expect(filteredSource.inlineData).toBe(baseSource.inlineData);
    // 层仍在（只改 filter，不增删层）
    expect(filtered.layers.length).toBe(base.layers.length);
    const baseFilter = JSON.stringify(base.layers[0].filter);
    const nextFilter = JSON.stringify(filtered.layers[0].filter);
    expect(nextFilter).toContain('武侯区');
    expect(nextFilter).not.toEqual(baseFilter);
  });

  it('clearing the selection restores the unfiltered composition', () => {
    const layers = [hudLayer('district-choropleth')];
    const compose = (selectionFilters?: Record<string, unknown[]>) =>
      hudStateToMapSpec({ layers, processLayers: {}, activeFilters: {}, selectionFilters, is3D: false });
    const base = compose();
    const withSelection = compose({
      'district-choropleth': ['in', ['get', 'district'], '武侯区'],
    });
    const cleared = compose(undefined);
    expect(JSON.stringify(cleared.layers[0].filter)).toBe(JSON.stringify(base.layers[0].filter));
    expect(JSON.stringify(withSelection.layers[0].filter)).not.toBe(JSON.stringify(base.layers[0].filter));
  });
});

describe('map → chart highlight', () => {
  beforeEach(() => {
    resetSelectionStore();
  });

  it('matching map selection highlights the category via selectionField', () => {
    publishSelection('select', {
      source: 'map',
      layer_id: 'district-choropleth',
      selected_ids: ['f1'],
      properties: { district: '锦江区' },
    });
    render(<>{renderComponent(chartComponent(), CTX)}</>);
    // 未命中类别降透明度（视觉对比而非隐藏）
    const cells = document.querySelectorAll('.recharts-cell');
    expect(cells.length).toBeGreaterThan(0);
    const fills = Array.from(cells).map((c) => c.getAttribute('fill'));
    expect(fills).toContain('rgba(6,182,212,0.25)');
    expect(fills).toContain('#06b6d4');
  });

  it('selection on a different layer does not highlight this chart', () => {
    publishSelection('select', {
      source: 'map',
      layer_id: 'poi-heat',
      properties: { district: '锦江区' },
    });
    render(<>{renderComponent(chartComponent(), CTX)}</>);
    const cells = document.querySelectorAll('.recharts-cell');
    const fills = Array.from(cells).map((c) => c.getAttribute('fill'));
    expect(fills).not.toContain('rgba(6,182,212,0.25)');
  });

  it('chart without selectionField degrades to state-only highlight (no map filter)', () => {
    const component = chartComponent({ selectionField: undefined });
    delete (component.options as Record<string, unknown>).selectionField;
    render(<>{renderComponent(component, CTX)}</>);
    const bars = document.querySelectorAll('[data-testid="chart-bar"]');
    fireEvent.click(bars[0]);
    const sel = getSelection();
    expect(sel?.selected_categories.length).toBe(1);
    expect(sel?.filter_field).toBeUndefined(); // 无过滤投影 → 仅状态高亮
  });
});
