/**
 * ChartCore 冒烟测试：chat 消息与地图 chart_panel 共用的渲染核。
 * recharts 以 mock 面替换（同 chart-theme.test.tsx 模式）。
 */
import { describe, it, expect, vi } from 'vitest';
import { render, screen } from '@testing-library/react';
import React from 'react';

let sawBarData: unknown = null;
vi.mock('recharts', async () => {
  const React = await import('react');
  const passthrough = ({ children }: { children?: React.ReactNode }) =>
    React.createElement('div', null, children);
  return {
    ResponsiveContainer: passthrough,
    BarChart: ({ data }: { data?: unknown }) => {
      sawBarData = data;
      return React.createElement('div', { 'data-testid': 'core-bar-chart' });
    },
    LineChart: passthrough,
    PieChart: passthrough,
    ScatterChart: passthrough,
    CartesianGrid: passthrough,
    XAxis: passthrough,
    YAxis: passthrough,
    Tooltip: passthrough,
    Legend: passthrough,
    Bar: passthrough,
    Line: passthrough,
    Pie: passthrough,
    Cell: passthrough,
    Scatter: passthrough,
  };
});

import { ChartCore, isChartTypeSupported } from './chart-core';

describe('ChartCore', () => {
  it('bar 图按类型分发渲染核并透传 data', () => {
    render(<ChartCore chart={{ type: 'bar', title: 't', data: [{ name: 'a', value: 1 }] }} />);
    expect(screen.getByTestId('core-bar-chart')).toBeTruthy();
    expect(sawBarData).toEqual([{ name: 'a', value: 1 }]);
  });

  it('未知类型 → null（调用方负责降级卡片）', () => {
    const { container } = render(
      <ChartCore chart={{ type: 'radar' as 'bar', title: 't', data: [] }} />,
    );
    expect(container.firstChild).toBeNull();
    expect(isChartTypeSupported('radar')).toBe(false);
    expect(isChartTypeSupported('bar')).toBe(true);
  });
});
