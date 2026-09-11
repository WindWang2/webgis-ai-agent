/**
 * SystemSettings「集群健康」tab 入口测试（ADR-0142 D1 —— 唯一 append 点）。
 */
import { describe, it, expect, vi, afterEach } from 'vitest';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import { SystemSettings } from './system-settings';
import {
  healthBasicFixture,
  readyOkFixture,
  detailedStatusOkFixture,
  versionInfoFixture,
  activeJobsFixture,
} from '@/test/fixtures/health-fixtures';
import { clusterMetricsFixture } from '@/test/fixtures/geocompute-fixtures';

describe('SystemSettings（P5 tab 入口）', () => {
  afterEach(() => vi.unstubAllGlobals());

  it('默认「常规」tab：既有内容不回归', () => {
    render(<SystemSettings />);
    expect(screen.getByText('Backend API URL')).toBeInTheDocument();
    expect(screen.getByRole('tab', { name: '常规' })).toHaveAttribute('aria-selected', 'true');
    expect(screen.getByRole('tab', { name: '集群健康' })).toHaveAttribute('aria-selected', 'false');
  });

  it('切到「集群健康」→ SystemHealthPanel 挂载（数据通道接通）', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(async (input: RequestInfo | URL) => {
        const url = new URL(String(input));
        const json = (body: unknown, status = 200) =>
          new Response(JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } });
        if (url.pathname === '/api/v1/health') return json(healthBasicFixture);
        if (url.pathname === '/api/v1/ready') return json(readyOkFixture);
        if (url.pathname === '/api/v1/status/detailed') return json(detailedStatusOkFixture);
        if (url.pathname === '/api/v1/version') return json(versionInfoFixture);
        if (url.pathname === '/api/v1/tasks/jobs') return json(activeJobsFixture);
        if (url.pathname === '/api/v1/geocompute/cluster/metrics') return json(clusterMetricsFixture);
        throw new Error(`no route: ${url.pathname}`);
      }),
    );
    render(<SystemSettings />);
    fireEvent.click(screen.getByRole('tab', { name: '集群健康' }));
    await waitFor(() => expect(screen.getByTestId('ops-system-health')).toBeInTheDocument());
    expect(screen.getByTestId('ops-health-basic')).toBeInTheDocument();
    // 既有常规内容卸载（tab 切换即卸载语义）
    expect(screen.queryByText('Backend API URL')).not.toBeInTheDocument();
  });
});
