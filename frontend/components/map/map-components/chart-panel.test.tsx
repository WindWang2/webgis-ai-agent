/**
 * chart_panel 渲染器测试（D2）：inline ChartData / chartRef 拉取 / variant
 * 回退 / 失败与空态降级。recharts 以 passthrough mock 面替换（jsdom 下
 * 真实渲染依赖容器尺寸，不稳定 —— 同 chart-theme.test.tsx 模式）。
 */
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import React from 'react';

vi.mock('recharts', async () => {
  const React = await import('react');
  const passthrough = ({ children }: { children?: React.ReactNode }) =>
    React.createElement('div', null, children);
  return {
    ResponsiveContainer: passthrough,
    BarChart: passthrough,
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

vi.mock('@/lib/api/transport', () => {
  class ApiError extends Error {
    status: number;
    body: unknown;
    constructor(status: number, body: unknown) {
      super(`API ${status}`);
      this.status = status;
      this.body = body;
    }
  }
  return { apiFetch: vi.fn(), ApiError };
});

import { renderComponent } from './index';
import { getComponentRenderer } from './registry';
import { resetChartArtifactCache } from '@/lib/map-components/chart-artifact';
import { setMapSpecSessionCursor } from '@/lib/mapspec/session-cursor';
import { apiFetch } from '@/lib/api/transport';
import type { MapSpecComponent } from '@/lib/mapspec-compiler/types';

const apiFetchMock = vi.mocked(apiFetch);

const VALID_BAR = { type: 'bar', title: '各区小学数量', data: [{ name: '锦江区', value: 12 }] };

function chartComp(options: Record<string, unknown>): MapSpecComponent {
  return { id: 'chart-1', type: 'chart_panel', enabled: true, options };
}

function renderPanel(options: Record<string, unknown>) {
  return render(
    <>
      {renderComponent(chartComp(options), { spec: null, zoom: 10, centerLat: 30, bearing: 0 })}
    </>,
  );
}

beforeEach(() => {
  apiFetchMock.mockReset();
  resetChartArtifactCache();
  setMapSpecSessionCursor(undefined);
});

describe('chart_panel renderer', () => {
  it('chart_panel 在 registry 注册（side-effect import）', () => {
    expect(getComponentRenderer('chart_panel')).toBeDefined();
  });

  it('inline bar 渲染：标题取 chart.title，走 ChartCore', () => {
    renderPanel({ chart: VALID_BAR });
    const panel = screen.getByTestId('spec-chrome-chart-panel');
    expect(panel).toBeTruthy();
    expect(panel.getAttribute('data-variant')).toBe('default');
    expect(screen.getByText('各区小学数量')).toBeTruthy();
    // inline 路径不发请求
    expect(apiFetchMock).not.toHaveBeenCalled();
  });

  it('compact variant：data-variant=compact', () => {
    renderPanel({ chart: VALID_BAR, variant: 'compact' });
    expect(screen.getByTestId('spec-chrome-chart-panel').getAttribute('data-variant')).toBe('compact');
  });

  it('非法 inline chart → 图表数据不可用 降级卡片（不崩）', () => {
    renderPanel({ chart: { type: 'bar', title: 'x', data: [] } });
    expect(screen.getByText('图表数据不可用')).toBeTruthy();
  });

  it('无 chart/chartRef → 暂无图表数据', () => {
    renderPanel({});
    expect(screen.getByText('暂无图表数据')).toBeTruthy();
  });

  it('未知 variant 确定性回退 default', () => {
    renderPanel({ chart: VALID_BAR, variant: 'definitely_not_a_variant' });
    expect(screen.getByTestId('spec-chrome-chart-panel').getAttribute('data-variant')).toBe('default');
  });

  it('chartRef：经会话 cursor 拉取 chart-artifacts 并渲染（ownerToken 随行）', async () => {
    setMapSpecSessionCursor('sid-chart', 5, 'owner-token-1');
    apiFetchMock.mockResolvedValue({ chart: VALID_BAR });
    renderPanel({ chartRef: 'ref:chart-abc' });

    expect(await screen.findByText('各区小学数量')).toBeTruthy();
    expect(apiFetchMock).toHaveBeenCalledTimes(1);
    const [url, init] = apiFetchMock.mock.calls[0];
    expect(String(url)).toContain('/api/v1/chat/sessions/sid-chart/chart-artifacts/ref%3Achart-abc');
    expect(init).toMatchObject({ ownerToken: 'owner-token-1' });
  });

  it('chartRef 拉取失败 → 图表数据不可用', async () => {
    setMapSpecSessionCursor('sid-chart', 5, null);
    apiFetchMock.mockRejectedValue(new Error('boom'));
    renderPanel({ chartRef: 'ref:chart-missing' });
    await waitFor(() => {
      expect(screen.getByText('图表数据不可用')).toBeTruthy();
    });
  });
});
