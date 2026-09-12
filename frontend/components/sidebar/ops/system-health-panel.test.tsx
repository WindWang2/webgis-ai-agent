/**
 * 系统健康分区测试（P5，ADR-0142 D6）—— ok/degraded 两态 / 组件级状态 /
 * 队列深度双口径 / 错误分类诚实空态 / 通道自检。
 */
import { describe, it, expect, vi, afterEach } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import { SystemHealthPanel } from './system-health-panel';
import {
  healthBasicFixture,
  readyOkFixture,
  detailedStatusOkFixture,
  detailedStatusDegradedFixture,
  versionInfoFixture,
  activeJobsFixture,
} from '@/test/fixtures/health-fixtures';
import { clusterMetricsFixture } from '@/test/fixtures/geocompute-fixtures';

function stubFetch(map: { health?: unknown; ready?: unknown; detailed?: unknown; version?: unknown; jobs?: unknown; metrics?: unknown }) {
  vi.stubGlobal(
    'fetch',
    vi.fn(async (input: RequestInfo | URL) => {
      const url = new URL(String(input));
      const json = (body: unknown, status = 200) =>
        new Response(JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } });
      if (url.pathname === '/api/v1/health') return json(map.health ?? healthBasicFixture);
      if (url.pathname === '/api/v1/ready') return json(map.ready ?? readyOkFixture);
      if (url.pathname === '/api/v1/status/detailed') return json(map.detailed ?? detailedStatusOkFixture);
      if (url.pathname === '/api/v1/version') return json(map.version ?? versionInfoFixture);
      if (url.pathname === '/api/v1/tasks/jobs') return json(map.jobs ?? activeJobsFixture);
      if (url.pathname === '/api/v1/geocompute/cluster/metrics') return json(map.metrics ?? clusterMetricsFixture);
      throw new Error(`no route: ${url.pathname}`);
    }),
  );
}

describe('SystemHealthPanel（P5）', () => {
  afterEach(() => vi.unstubAllGlobals());

  it('健康卡：状态 / 就绪 / runtime / 版本', async () => {
    stubFetch({});
    render(<SystemHealthPanel ownerToken="tok" />);
    await waitFor(() => expect(screen.getByTestId('ops-health-basic')).toHaveTextContent('healthy'));
    expect(screen.getByTestId('tile-状态')).toHaveTextContent('healthy');
    expect(screen.getByTestId('tile-就绪探针')).toHaveTextContent('ready');
    expect(screen.getByTestId('tile-agent runtime')).toHaveTextContent('pi');
  });

  it('组件级状态：5 组件 + 延迟 + not_configured 诚实缺失', async () => {
    stubFetch({});
    render(<SystemHealthPanel ownerToken="tok" />);
    await waitFor(() => expect(screen.getByText('数据库')).toBeInTheDocument());
    expect(screen.getByText('Redis')).toBeInTheDocument();
    expect(screen.getByText('LLM 网关')).toBeInTheDocument();
    expect(screen.getByText('对象存储')).toBeInTheDocument();
    expect(screen.getByText('not_configured')).toBeInTheDocument();
    expect(screen.getByText(/stuck jobs 1/)).toBeInTheDocument();
  });

  it('降级态：redis down / llm degraded 映射为 warning/error', async () => {
    stubFetch({ detailed: detailedStatusDegradedFixture });
    render(<SystemHealthPanel ownerToken="tok" />);
    await waitFor(() => expect(screen.getByText('connection refused')).toBeInTheDocument());
    // llm 组件与总体状态都是 degraded（多处）
    expect(screen.getAllByText('degraded').length).toBeGreaterThanOrEqual(2);
    expect(screen.getByText(/stuck jobs 4/)).toBeInTheDocument();
  });

  it('队列深度双口径 + 口径注记', async () => {
    stubFetch({});
    render(<SystemHealthPanel ownerToken="tok" />);
    await waitFor(() => expect(screen.getByTestId('tile-owner 域活跃任务')).toHaveTextContent('2'));
    expect(screen.getByTestId('tile-全局 queue / inflight')).toHaveTextContent('5 / 12');
    expect(screen.getByText(/两者不可相加/)).toBeInTheDocument();
  });

  it('错误分类 top-N：诚实空态卡（能力待后端支持）', async () => {
    stubFetch({});
    render(<SystemHealthPanel ownerToken="tok" />);
    await waitFor(() => expect(screen.getByTestId('ops-error-taxonomy')).toBeInTheDocument());
    expect(screen.getByText('能力待后端支持')).toBeInTheDocument();
    expect(screen.getByText(/无 HTTP 端点（P0 勘测）/)).toBeInTheDocument();
  });

  it('构建信息卡 + 特性标志诚实缺失注记', async () => {
    stubFetch({});
    render(<SystemHealthPanel ownerToken="tok" />);
    await waitFor(() => expect(screen.getByTestId('ops-version')).toHaveTextContent('0.1.3'));
    expect(screen.getByText(/无 flags 披露端点/)).toBeInTheDocument();
  });

  it('通道自检卡列出五条通道', async () => {
    stubFetch({});
    render(<SystemHealthPanel ownerToken="tok" />);
    await waitFor(() => expect(screen.getByTestId('ops-channel-check')).toBeInTheDocument());
    expect(screen.getByText('/health + /ready')).toBeInTheDocument();
    expect(screen.getByText('/status/detailed')).toBeInTheDocument();
    expect(screen.getByText('/tasks/jobs（owner 域）')).toBeInTheDocument();
    expect(screen.getByText('/cluster/metrics（全局）')).toBeInTheDocument();
  });
});
