import { render, screen, waitFor } from '@testing-library/react';
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { RecoveryBanner, type RecoverySummary } from './recovery-banner';

// 面板测试的接缝是组件本身（spec Testing Decisions #2，同 session-panel）：
// 水合结果由 mock 的 apiFetch 控制；`?recovery=1` 的 URL 契约在此一并钉住。
const apiFetch = vi.hoisted(() => vi.fn());
vi.mock('@/lib/api/transport', () => ({ apiFetch }));

const FULL: RecoverySummary = {
  status: 'ok',
  recovered: true,
  recovered_to_revision: 9,
  layer_count: 4,
  invalid_source_refs: [
    { source: 'src-dead', ref: 'ref-dead-xxxx' },
    { source: 'src-gone', ref: 'ref-gone-yyyy' },
  ],
  durable_jobs: [
    { id: '424242', status: 'running' },
    { id: '424241', status: 'cancelling' },
  ],
  user_constraints: {
    locked_layers: ['l-keep'],
    locked_components: 2,
    hidden_layers: ['l-hidden', 'l-legacy-hidden'],
    label_overrides: 3,
    active_frame: 'frame-a',
  },
};

function mockHydration(recovery: RecoverySummary | undefined | Error) {
  apiFetch.mockImplementationOnce(() =>
    recovery instanceof Error ? Promise.reject(recovery) : Promise.resolve({ recovery }),
  );
}

describe('RecoveryBanner — 恢复可读性短语（G22-P6）', () => {
  beforeEach(() => {
    apiFetch.mockReset();
  });

  it('renders all phrases from a fully-populated summary', async () => {
    mockHydration(FULL);
    render(<RecoveryBanner sessionId="s1" ownerToken="tok-1" />);
    await waitFor(() =>
      expect(screen.getByText('会话已恢复 · 地图 revision 9')).toBeInTheDocument(),
    );
    expect(screen.getByText('2 个数据引用已失效')).toBeInTheDocument();
    // cancelling 在非终态集合内 → 如实披露「取消中」，不伪装成纯运行。
    expect(screen.getByText('2 个任务继续运行/取消中')).toBeInTheDocument();
    expect(
      screen.getByText('已保留人工约束（隐藏 2 层 · 锁定 1 层 · 标注 3 条）'),
    ).toBeInTheDocument();
  });

  it('omits phrases whose facts are empty (no lying)', async () => {
    mockHydration({
      recovered: true,
      recovered_to_revision: 4,
      layer_count: 2,
      invalid_source_refs: [],
      durable_jobs: [],
      user_constraints: { locked_layers: [], hidden_layers: [], label_overrides: 0 },
    });
    const { container } = render(<RecoveryBanner sessionId="s1" />);
    await waitFor(() =>
      expect(screen.getByText('会话已恢复 · 地图 revision 4')).toBeInTheDocument(),
    );
    expect(container.textContent).not.toContain('数据引用已失效');
    expect(container.textContent).not.toContain('任务继续运行');
    expect(container.textContent).not.toContain('已保留人工约束');
  });

  it('drops the revision suffix when the spec carries none', async () => {
    mockHydration({ recovered: true, recovered_to_revision: null });
    render(<RecoveryBanner sessionId="s1" />);
    await waitFor(() => expect(screen.getByText('会话已恢复')).toBeInTheDocument());
    expect(screen.queryByText(/地图 revision/)).not.toBeInTheDocument();
  });

  it('keeps technical ids out of visible text; they live in the title only', async () => {
    mockHydration(FULL);
    const { container } = render(<RecoveryBanner sessionId="s1" />);
    await waitFor(() => expect(screen.getByTestId('recovery-banner')).toBeInTheDocument());
    // 可见文本无任何技术 id。
    for (const id of ['src-dead', 'ref-dead-xxxx', 'src-gone', '424242', '424241', 'l-hidden', 'l-keep', 'frame-a']) {
      expect(container.textContent).not.toContain(id);
    }
    // 诊断面：id 只出现在 title 属性（getByTitle 可寻址）。
    const banner = screen.getByTitle(/src-dead→ref-dead-xxxx/);
    expect(banner.getAttribute('title')).toContain('424242:running');
    expect(banner.getAttribute('title')).toContain('l-hidden');
    expect(banner.getAttribute('title')).toContain('active_frame=frame-a');
  });

  it('is entirely hidden when not recovered, on fetch failure, or without a session', async () => {
    // 未恢复（空会话 / 已删除）→ 整条隐藏。
    mockHydration({ recovered: false });
    const { container } = render(<RecoveryBanner sessionId="s1" />);
    await waitFor(() => expect(apiFetch).toHaveBeenCalled());
    await waitFor(() => expect(container.firstChild).toBeNull());

    // 拉取失败 → 整条隐藏（降级不阻塞聊天面）。
    mockHydration(new Error('network down'));
    const view2 = render(<RecoveryBanner sessionId="s1" />);
    await waitFor(() => expect(view2.container.firstChild).toBeNull());

    // 无会话 → 不发请求且隐藏。
    apiFetch.mockClear();
    const view3 = render(<RecoveryBanner sessionId={null} />);
    expect(view3.container.firstChild).toBeNull();
    expect(apiFetch).not.toHaveBeenCalled();
  });

  it('fetches with ?recovery=1 and owner token, and re-hydrates per session switch', async () => {
    mockHydration(FULL);
    const { rerender } = render(<RecoveryBanner sessionId="s1" ownerToken="tok-1" />);
    await waitFor(() => expect(apiFetch).toHaveBeenCalledTimes(1));
    expect(apiFetch).toHaveBeenLastCalledWith(
      '/api/v1/chat/sessions/s1?recovery=1',
      { ownerToken: 'tok-1' },
    );

    mockHydration({ recovered: true, recovered_to_revision: 2 });
    rerender(<RecoveryBanner sessionId="s2" ownerToken="tok-1" />);
    await waitFor(() => expect(apiFetch).toHaveBeenCalledTimes(2));
    expect(apiFetch).toHaveBeenLastCalledWith(
      '/api/v1/chat/sessions/s2?recovery=1',
      { ownerToken: 'tok-1' },
    );
    // 陈旧会话的摘要绝不涂到新会话上。
    await waitFor(() =>
      expect(screen.getByText('会话已恢复 · 地图 revision 2')).toBeInTheDocument(),
    );
    expect(screen.queryByText('地图 revision 9')).not.toBeInTheDocument();
  });
});
