/**
 * G3D05（ADR-0275）elevation_profile 渲染器测试：inline points 折线 /
 * 不足 2 点占位 / profileRef 工件通道（同 chart_panel：apiFetch mock +
 * 会话 cursor）。recharts 以 passthrough mock 面替换（jsdom 下真实渲染
 * 依赖容器尺寸 —— 同 chart-panel.test.tsx 模式）。
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
    AreaChart: passthrough,
    Area: passthrough,
    RadialBarChart: passthrough,
    RadialBar: passthrough,
    RadarChart: passthrough,
    Radar: passthrough,
    PolarGrid: passthrough,
    PolarAngleAxis: passthrough,
    PolarRadiusAxis: passthrough,

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

const PROFILE_POINTS = [
  { distance_m: 0, elevation_m: 512 },
  { distance_m: 1500, elevation_m: 603 },
  { distance_m: 4200, elevation_m: 748 },
];

function profileComp(options: Record<string, unknown>): MapSpecComponent {
  return { id: 'elevation-profile-1', type: 'elevation_profile', enabled: true, options };
}

function renderPanel(options: Record<string, unknown>) {
  return render(
    <>
      {renderComponent(profileComp(options), { spec: null, zoom: 10, centerLat: 30, bearing: 0 })}
    </>,
  );
}

beforeEach(() => {
  apiFetchMock.mockReset();
  resetChartArtifactCache();
  setMapSpecSessionCursor(undefined);
});

describe('elevation_profile renderer', () => {
  it('在 registry 注册（side-effect import）', () => {
    expect(getComponentRenderer('elevation_profile')).toBeDefined();
  });

  it('inline points：标题带基准后缀 + 剖面图容器在场（不发请求）', () => {
    renderPanel({
      points: PROFILE_POINTS,
      unit: 'm',
      verticalDatum: 'EGM96',
    });
    const panel = screen.getByTestId('spec-chrome-elevation-profile');
    expect(panel).toBeTruthy();
    expect(panel.getAttribute('data-variant')).toBe('default');
    expect(screen.getByText('高程剖面（基准：EGM96）')).toBeTruthy();
    const chart = screen.getByTestId('elevation-profile-chart');
    expect(chart).toBeTruthy();
    expect(chart.getAttribute('role')).toBe('img');
    expect(chart.getAttribute('aria-label')).toBe('高程剖面');
    expect(chart.getAttribute('data-unit')).toBe('m');
    expect(chart.getAttribute('data-datum')).toBe('EGM96');
    // inline 路径不发请求
    expect(apiFetchMock).not.toHaveBeenCalled();
  });

  it('无 verticalDatum → 标题无基准后缀', () => {
    renderPanel({ points: PROFILE_POINTS });
    expect(screen.getByText('高程剖面')).toBeTruthy();
    expect(screen.queryByText(/基准：/)).toBeNull();
  });

  it('points < 2 → 剖面数据不足占位（不画图）', () => {
    renderPanel({ points: [{ distance_m: 0, elevation_m: 100 }] });
    expect(screen.getByText('剖面数据不足')).toBeTruthy();
    expect(screen.queryByTestId('elevation-profile-chart')).toBeNull();
  });

  it('无 points 且无 profileRef → 剖面数据不足占位', () => {
    renderPanel({});
    expect(screen.getByText('剖面数据不足')).toBeTruthy();
    expect(apiFetchMock).not.toHaveBeenCalled();
  });

  it('profileRef：经 chart-artifact 通道拉取 ref:profile-* 并渲染', async () => {
    setMapSpecSessionCursor('sid-profile', 5, 'owner-token-1');
    apiFetchMock.mockResolvedValue({
      chart: {
        type: 'line',
        title: '高程剖面',
        data: [
          { name: '0m', value: 512 },
          { name: '1.50km', value: 603 },
        ],
      },
    });
    renderPanel({ profileRef: 'ref:profile-abc' });

    expect(await screen.findByTestId('elevation-profile-chart')).toBeTruthy();
    expect(apiFetchMock).toHaveBeenCalledTimes(1);
    const [url, init] = apiFetchMock.mock.calls[0];
    expect(String(url)).toContain('/api/v1/chat/sessions/sid-profile/chart-artifacts/ref%3Aprofile-abc');
    expect(init).toMatchObject({ ownerToken: 'owner-token-1' });
  });

  it('profileRef 拉取失败 → 剖面数据不可用（ref 未绑定）诚实占位', async () => {
    setMapSpecSessionCursor('sid-profile', 5, null);
    apiFetchMock.mockRejectedValue(new Error('boom'));
    renderPanel({ profileRef: 'ref:profile-missing' });
    await waitFor(() => {
      expect(screen.getByText('剖面数据不可用（ref 未绑定）')).toBeTruthy();
    });
    expect(screen.queryByTestId('elevation-profile-chart')).toBeNull();
  });

  it('profileRef 载荷不足 2 点 → 同样诚实占位（不拿单点画剖面）', async () => {
    setMapSpecSessionCursor('sid-profile', 5, null);
    apiFetchMock.mockResolvedValue({
      chart: { type: 'line', title: 'x', data: [{ name: '0m', value: 1 }] },
    });
    renderPanel({ profileRef: 'ref:profile-thin' });
    await waitFor(() => {
      expect(screen.getByText('剖面数据不可用（ref 未绑定）')).toBeTruthy();
    });
  });

  it('compact variant → data-variant=compact', () => {
    renderPanel({ points: PROFILE_POINTS, variant: 'compact' });
    expect(screen.getByTestId('spec-chrome-elevation-profile').getAttribute('data-variant')).toBe('compact');
  });
});
