import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent, act } from '@testing-library/react';
import { ToolCallChain, type ToolCallEntry } from './tool-call-card';

/**
 * #1000 ① — 含失败行的工具调用链默认展开。此前失败详情埋在
 * 「展开调用链 → 展开单行」两级折叠之下，折叠摘要只有『N 个失败』图标，
 * 用户默认看不到失败原因，难以调整后重试。
 */
vi.mock('@/lib/store/useHudStore', () => ({
  useHudStore: (selector: (s: any) => any) =>
    selector({
      focusLayer: vi.fn(),
      layers: [],
    }),
}));

function chainButton() {
  return screen.getByRole('button', { name: /工具调用链|个工具调用完成/ });
}

describe('ToolCallChain — failure auto-expansion (#1000)', () => {
  beforeEach(() => vi.clearAllMocks());

  it('mounts expanded when the chain already contains a failed row (restored history)', () => {
    render(
      <ToolCallChain
        calls={[
          { id: 'tc-1', tool: 'geocode', status: 'completed' },
          { id: 'tc-2', tool: 'geocode_cn', status: 'failed', error: '上游超时' },
        ]}
      />,
    );
    // 链级 region 直接可见，无需第一次点击
    expect(screen.getByRole('region', { name: '工具调用链详情' })).toBeInTheDocument();
    // 行级工具名可见（geocode → 『地理编码』）
    expect(screen.getAllByText('地理编码').length).toBeGreaterThan(0);
  });

  it('stays collapsed by default when all rows completed (no regression)', () => {
    render(
      <ToolCallChain
        calls={[
          { id: 'tc-1', tool: 'geocode', status: 'completed' },
          { id: 'tc-2', tool: 'geocode_cn', status: 'completed' },
        ]}
      />,
    );
    expect(screen.queryByRole('region', { name: '工具调用链详情' })).toBeNull();
  });

  it('auto-expands once when a failure lands mid-stream (running → failed)', async () => {
    const initial: ToolCallEntry[] = [{ id: 'tc-1', tool: 'geocode', status: 'running' }];
    const { rerender } = render(<ToolCallChain calls={initial} />);
    expect(screen.queryByRole('region', { name: '工具调用链详情' })).toBeNull();

    await act(async () => {
      rerender(
        <ToolCallChain
          calls={[{ id: 'tc-1', tool: 'geocode', status: 'failed', error: 'geocode 超时' }]}
        />,
      );
    });
    expect(screen.getByRole('region', { name: '工具调用链详情' })).toBeInTheDocument();
  });

  it('user collapse wins over auto-expand (explicit interaction is respected)', async () => {
    const { rerender } = render(
      <ToolCallChain calls={[{ id: 'tc-1', tool: 'geocode', status: 'failed' }]} />,
    );
    expect(screen.getByRole('region', { name: '工具调用链详情' })).toBeInTheDocument();

    await act(async () => {
      fireEvent.click(chainButton());
    });
    expect(screen.queryByRole('region', { name: '工具调用链详情' })).toBeNull();

    // 后续状态刷新（props 变化重渲染）不强推展开
    await act(async () => {
      rerender(
        <ToolCallChain
          calls={[{ id: 'tc-1', tool: 'geocode', status: 'failed', error: '仍失败' }]}
        />,
      );
    });
    expect(screen.queryByRole('region', { name: '工具调用链详情' })).toBeNull();
  });
});
