/**
 * chart_panel × MVT 视口聚合（Track B · M7/M8）DOM 契约：
 * - MVT 层 + extentLinked + selectionField：图表数据点替换为服务端聚合计数，
 *   证据行（扫描/匹配/分组/revision）与 truncated 徽标随行；
 * - inline 层 extentLinked 走既有本地过滤路径（回归护栏：不触发聚合请求）；
 * - 类别点击照常发布 chart 选择（联动不被聚合模式破坏）。
 */
import { describe, expect, it, beforeEach, vi } from 'vitest';
import { render, screen, waitFor, act } from '@testing-library/react';
import React from 'react';
import type { MapSpecComponent } from '@/lib/mapspec-compiler/types';

// recharts passthrough（jsdom 容器尺寸为零，真实渲染不稳定 —— 同
// chart-panel.test.tsx 模式；数据值经子组件文本可断言）。
vi.mock('recharts', async () => {
  const React = await import('react');
  const passthrough = ({ children }: { children?: React.ReactNode }) =>
    React.createElement('div', null, children);
  return {
    ResponsiveContainer: passthrough, BarChart: passthrough, LineChart: passthrough,
    PieChart: passthrough, ScatterChart: passthrough, CartesianGrid: passthrough,
    XAxis: passthrough, YAxis: passthrough, Tooltip: passthrough, Legend: passthrough,
    Bar: passthrough, Line: passthrough, Pie: passthrough, Cell: passthrough,
    AreaChart: passthrough, Area: passthrough, RadialBarChart: passthrough,
    RadialBar: passthrough, RadarChart: passthrough, Radar: passthrough,
    PolarGrid: passthrough, PolarAngleAxis: passthrough, PolarRadiusAxis: passthrough,
    Scatter: passthrough,
  };
});
import {
  getSelection,
  resetSelectionStore,
} from '@/lib/selection/selection-store';
import {
  publishViewportContext,
  resetViewportContext,
} from '@/lib/selection/viewport-context';
import { useHudStore } from '@/lib/store/useHudStore';
import { renderComponent } from './index';

vi.mock('@/lib/data-plane/paged-features', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@/lib/data-plane/paged-features')>();
  return {
    ...actual,
    fetchLayerAggregate: vi.fn(),
  };
});

import { fetchLayerAggregate } from '@/lib/data-plane/paged-features';

const mockAgg = vi.mocked(fetchLayerAggregate);

// ChartCore 数据接缝：passthrough recharts 不渲染数据值 —— 以 spy 暴露
// 实际喂给图表的数据点（聚合替换断言的精确接缝）。
const chartCoreSpy = vi.hoisted(() => vi.fn(
  ({ chart }: { chart: { data: Array<{ name: string; value: number }> } }) =>
    React.createElement('div', { 'data-testid': 'chart-core-data' },
      chart.data.map((p) => `${p.name}=${p.value}`).join(';'))));
vi.mock('@/components/chat/chart-core', () => ({ ChartCore: chartCoreSpy }));

vi.mock('@/lib/mapspec/component-mutation', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@/lib/mapspec/component-mutation')>();
  return {
    ...actual,
    commitComponentPatch: vi.fn().mockResolvedValue(undefined),
  };
});

function chartComponent(options: Record<string, unknown>): MapSpecComponent {
  return {
    id: 'chart-panel',
    type: 'chart_panel',
    enabled: true,
    placement: { mode: 'floating', x: 32, y: 96, width: 360, height: 260 },
    options,
  } as unknown as MapSpecComponent;
}

function seedMvtLayer() {
  useHudStore.getState().addLayer?.({
    id: 'mvt-layer',
    name: '成都学校',
    type: 'circle',
    visible: true,
    source: { type: 'vector' },
    _tileUrl: '/api/v1/layers/data/ref:schools/tiles/{z}/{x}/{y}.mvt?session_id=session-chart-000001',
    _refId: 'ref:schools',
    _descriptor: { mvt_capable: true, feature_count: 90000, content_revision: 6 },
  } as never);
}

const RendererContext = { spec: null, zoom: 10, centerLat: 30 } as never;

const BASE_CHART = {
  type: 'bar',
  title: '学校类型分布',
  data: [
    { name: '小学', value: 999 },
    { name: '中学', value: 888 },
    { name: '幼儿园', value: 777 },
  ],
};

/** 等待 viewport-context 的 300ms debounce 结算（真实定时器）。 */
async function settleViewport(bbox: [number, number, number, number], zoom = 10) {
  publishViewportContext(bbox, zoom);
  await act(async () => { await new Promise((r) => setTimeout(r, 420)); });
}

describe('chart_panel viewport aggregate (MVT layers)', () => {
  beforeEach(() => {
    resetSelectionStore();
    resetViewportContext();
    useHudStore.getState().clearLayers?.();
    mockAgg.mockReset();
  });

  it('MVT + extentLinked: server aggregate replaces data points with evidence line', async () => {
    seedMvtLayer();
    mockAgg.mockResolvedValueOnce({
      rows: [
        { key: '小学', count: 120 },
        { key: '中学', count: 80 },
        { key: '__other__', count: 9 },
      ],
      groupCount: 15,
      isComplete: false,
      truncated: true,
      scanned: 90000,
      totalMatching: 209,
      nullKeyCount: 0,
      nonNumericCount: 0,
      revision: 6,
      reasonCodes: ['aggregate_full_scan', 'bbox_coarse', 'groups_truncated'],
    });
    render(renderComponent(chartComponent({
      layerId: 'mvt-layer', selectionField: '类型', extentLinked: true, chart: BASE_CHART,
    }), RendererContext) as never);
    // 视口 settle → 触发聚合
    await settleViewport([104, 30, 105, 31]);
    await waitFor(() => {
      expect(mockAgg).toHaveBeenCalledTimes(1);
    });
    expect(mockAgg.mock.calls[0][0]).toMatchObject({
      refId: 'ref:schools', groupBy: '类型', revision: 6,
    });
    // 数据点 = 聚合计数（120/80/9），不再是 inline 的 999/888/777
    const dataText = await waitFor(() => {
      const el = screen.getByTestId('chart-core-data');
      expect(el.textContent).toContain('小学=120');
      expect(el.textContent).toContain('__other__=9');
      expect(el.textContent).not.toContain('999');
      return el.textContent ?? '';
    });
    expect(dataText).toBeTruthy();
    const evidence = screen.getByTestId('chart-agg-evidence').textContent ?? '';
    expect(evidence).toContain('90000');  // scanned
    expect(evidence).toContain('209');    // matched
    expect(evidence).toContain('15');     // groups
    expect(evidence).toContain('v6');     // revision
    expect(screen.getByTestId('chart-agg-truncated').textContent).toContain('合并');
  });

  it('inline layer extentLinked does NOT hit the aggregate endpoint (regression guard)', async () => {
    useHudStore.getState().addLayer?.({
      id: 'inline-layer',
      name: '小层',
      type: 'circle',
      visible: true,
      source: {
        type: 'FeatureCollection',
        features: [
          { type: 'Feature', geometry: { type: 'Point', coordinates: [104.1, 30.6] }, properties: { 类型: '小学' } },
          { type: 'Feature', geometry: { type: 'Point', coordinates: [110, 40] }, properties: { 类型: '中学' } },
        ],
      },
    } as never);
    render(renderComponent(chartComponent({
      layerId: 'inline-layer', selectionField: '类型', extentLinked: true, chart: BASE_CHART,
    }), RendererContext) as never);
    await settleViewport([104, 30, 104.2, 30.7]);
    await waitFor(() => {
      const el = document.querySelector('[data-testid="spec-chrome-chart-panel"]');
      expect(el?.textContent).toContain('学校类型分布');
    });
    expect(mockAgg).not.toHaveBeenCalled();
    // inline 本地过滤路径的证据行不存在（聚合证据行是 MVT 模式专属）
    expect(document.querySelector('[data-testid="chart-agg-evidence"]')).toBeNull();
  });

  it('aggregate failure shows honest error note, panel keeps chrome', async () => {
    seedMvtLayer();
    mockAgg.mockRejectedValueOnce(new Error('network down'));
    render(renderComponent(chartComponent({
      layerId: 'mvt-layer', selectionField: '类型', extentLinked: true, chart: BASE_CHART,
    }), RendererContext) as never);
    await settleViewport([104, 30, 105, 31]);
    await waitFor(() => {
      expect(screen.getByText('视口统计获取失败')).toBeTruthy();
    });
    // 图表面板本身不崩（inline 基线数据仍在）
    expect(document.querySelector('[data-testid="spec-chrome-chart-panel"]')?.textContent).toContain('学校类型分布');
  });

  it('extentLinked=false keeps inline artifact data untouched (no aggregate)', async () => {
    seedMvtLayer();
    render(renderComponent(chartComponent({
      layerId: 'mvt-layer', selectionField: '类型', extentLinked: false, chart: BASE_CHART,
    }), RendererContext) as never);
    await settleViewport([104, 30, 105, 31]);
    await waitFor(() => {
      expect(screen.getByTestId('chart-core-data').textContent).toContain('小学=999');
    });
    expect(mockAgg).not.toHaveBeenCalled();
  });

});
