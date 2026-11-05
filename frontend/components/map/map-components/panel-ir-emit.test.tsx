/**
 * ADR-0283 D2 面板接线测试：chart 类别点击 / table 行点击 → IR 上行。
 * ChartCore/表格以 spy 替身捕获 onSelectCategory/handleRowClick 接缝，
 * 断言 publishSelection 与 emit* 同刻发生（联动 + 上行不互相破坏）。
 */
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { render, screen, waitFor, act } from '@testing-library/react';
import React from 'react';
import type { MapSpecComponent } from '@/lib/mapspec-compiler/types';
import { resetSelectionStore, getSelection } from '@/lib/selection/selection-store';
import { renderComponent } from './index';

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

const emitChart = vi.hoisted(() => vi.fn());
const emitTable = vi.hoisted(() => vi.fn());
vi.mock('@/lib/interaction/bus', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@/lib/interaction/bus')>();
  return {
    ...actual,
    emitChartSelectionChanged: emitChart,
    emitTableSelectionChanged: emitTable,
  };
});

// ChartCore spy：捕获 onSelectCategory 接缝并直接调用（绕开 recharts）。
const chartCoreSpy = vi.hoisted(() => vi.fn(
  ({ chart }: { chart: { data: Array<{ name: string; value: number }> } }) =>
    React.createElement('div', { 'data-testid': 'chart-core' },
      chart.data.map((p) => `${p.name}`).join(','))));

vi.mock('@/components/chat/chart-core', () => ({ ChartCore: chartCoreSpy }));

vi.mock('@/lib/mapspec/component-mutation', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@/lib/mapspec/component-mutation')>();
  return { ...actual, commitComponentPatch: vi.fn().mockResolvedValue(undefined) };
});

function chartComponent(): MapSpecComponent {
  return {
    id: 'chart-panel', type: 'chart_panel', enabled: true,
    placement: { mode: 'floating', x: 32, y: 96, width: 360, height: 260 },
    options: {
      layerId: 'districts',
      selectionField: 'type',
      chart: {
        type: 'bar',
        title: '类型分布',
        data: [
          { name: 'primary', value: 3 },
          { name: 'middle', value: 2 },
        ],
      },
    },
  } as unknown as MapSpecComponent;
}

beforeEach(() => {
  resetSelectionStore();
  emitChart.mockClear();
  emitTable.mockClear();
  chartCoreSpy.mockClear();
});

describe('chart-panel category click → IR emit', () => {
  it('emits chart_selection_changed alongside publishSelection', async () => {
    const ctx = { spec: null, zoom: 10, centerLat: 30 } as never;
    const { container } = render(
      renderComponent(chartComponent(), ctx) as never,
    );
    await waitFor(() => {
      expect(chartCoreSpy).toHaveBeenCalled();
    });
    const props = chartCoreSpy.mock.calls.at(-1)?.[0] as {
      onSelectCategory?: (name: string) => void;
    };
    expect(props.onSelectCategory).toBeTruthy();
    act(() => {
      props.onSelectCategory!('primary');
    });
    // 联动照常（selection-store 收到 chart 选择）
    expect(getSelection()?.source).toBe('chart');
    // ADR-0283 D2：同刻上行 IR
    expect(emitChart).toHaveBeenCalledWith(
      expect.objectContaining({
        layerId: 'districts',
        field: 'type',
        categories: ['primary'],
      }),
    );
    expect(container).toBeTruthy();
  });
});
