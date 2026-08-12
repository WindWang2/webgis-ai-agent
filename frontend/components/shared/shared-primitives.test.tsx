import { describe, it, expect, vi, afterEach } from 'vitest';
import { act, render, screen, fireEvent } from '@testing-library/react';
import { Layers } from 'lucide-react';

/**
 * UI V3 shared primitives 契约测试。
 */

import { StatusBadge } from './status-badge';
import { ConfirmAction } from './confirm-action';
import { SearchField } from './search-field';
import { PanelHeader } from './panel-header';
import { EmptyState } from './empty-state';
import { InlineNotice } from './inline-notice';

afterEach(() => {
  vi.useRealTimers();
});

describe('StatusBadge', () => {
  it('maps the 8 durable job statuses to Chinese labels', () => {
    const expected: Record<string, string> = {
      pending: '等待中',
      queued: '排队中',
      running: '运行中',
      cancelling: '取消中',
      completed: '已完成',
      failed: '失败',
      cancelled: '已取消',
      stale: '已过期',
    };
    for (const [status, label] of Object.entries(expected)) {
      const { unmount } = render(<StatusBadge status={status} />);
      expect(screen.getByText(label)).toBeInTheDocument();
      unmount();
    }
  });

  it('falls back to the raw status text for unknown values', () => {
    render(<StatusBadge status="some_custom_state" />);
    expect(screen.getByText('some_custom_state')).toBeInTheDocument();
  });

  it('shows a reduced-motion-safe pulse dot only for transient states', () => {
    const { container, unmount } = render(<StatusBadge status="running" />);
    const dot = container.querySelector('.animate-pulse');
    expect(dot).not.toBeNull();
    expect(dot!.className).toContain('motion-reduce:animate-none');
    unmount();

    const { container: done } = render(<StatusBadge status="completed" />);
    expect(done.querySelector('.animate-pulse')).toBeNull();
  });
});

describe('ConfirmAction', () => {
  it('requires two clicks: first arms, second confirms', () => {
    vi.useFakeTimers();
    const onConfirm = vi.fn();
    render(<ConfirmAction label="删除" confirmLabel="确认删除？" onConfirm={onConfirm} />);

    const btn = screen.getByRole('button', { name: '删除' });
    fireEvent.click(btn);
    expect(onConfirm).not.toHaveBeenCalled();

    // 确认点击必须在 arm 最小间隔（250ms）之后，防双击一次手势完成删除
    act(() => vi.advanceTimersByTime(300));
    const armed = screen.getByRole('button', { name: '确认删除？' });
    fireEvent.click(armed);
    expect(onConfirm).toHaveBeenCalledTimes(1);
    // 确认后还原
    expect(screen.getByRole('button', { name: '删除' })).toBeInTheDocument();
  });

  it('ignores a confirm click within the min-arm window (double-click protection)', () => {
    const onConfirm = vi.fn();
    render(<ConfirmAction label="删除" confirmLabel="确认删除？" onConfirm={onConfirm} />);

    const btn = screen.getByRole('button', { name: '删除' });
    fireEvent.click(btn);
    // 立即第二击（双击手势）—— 不确认
    fireEvent.click(screen.getByRole('button', { name: '确认删除？' }));
    expect(onConfirm).not.toHaveBeenCalled();
  });

  it('auto-reverts after the timeout', () => {
    vi.useFakeTimers();
    render(<ConfirmAction label="删除" confirmLabel="确认删除？" onConfirm={vi.fn()} />);

    fireEvent.click(screen.getByRole('button', { name: '删除' }));
    expect(screen.getByRole('button', { name: '确认删除？' })).toBeInTheDocument();

    act(() => vi.advanceTimersByTime(3000));
    expect(screen.getByRole('button', { name: '删除' })).toBeInTheDocument();
  });
});

describe('SearchField', () => {
  it('fires onChange immediately when debounceMs=0', () => {
    const onChange = vi.fn();
    render(<SearchField value="" onChange={onChange} aria-label="搜索" debounceMs={0} />);
    fireEvent.change(screen.getByRole('searchbox', { name: '搜索' }), { target: { value: '北京' } });
    expect(onChange).toHaveBeenCalledWith('北京');
  });

  it('debounces onChange when debounceMs>0', () => {
    vi.useFakeTimers();
    const onChange = vi.fn();
    render(<SearchField value="" onChange={onChange} aria-label="搜索" debounceMs={300} />);

    fireEvent.change(screen.getByRole('searchbox', { name: '搜索' }), { target: { value: '北京市' } });
    expect(onChange).not.toHaveBeenCalled();

    act(() => vi.advanceTimersByTime(300));
    expect(onChange).toHaveBeenCalledWith('北京市');
  });

  it('Escape clears the draft and emits an empty value immediately', () => {
    vi.useFakeTimers();
    const onChange = vi.fn();
    render(<SearchField value="" onChange={onChange} aria-label="搜索" debounceMs={300} />);

    const input = screen.getByRole('searchbox', { name: '搜索' });
    fireEvent.change(input, { target: { value: 'abc' } });
    fireEvent.keyDown(input, { key: 'Escape' });
    expect(onChange).toHaveBeenCalledWith('');
    expect((input as HTMLInputElement).value).toBe('');
  });
});

describe('PanelHeader', () => {
  it('renders title, description and badge; close button fires onClose', () => {
    const onClose = vi.fn();
    render(<PanelHeader title="图层" description="可见性 · 样式 · 顺序" badge={3} onClose={onClose} />);

    expect(screen.getByText('图层')).toBeInTheDocument();
    expect(screen.getByText('可见性 · 样式 · 顺序')).toBeInTheDocument();
    expect(screen.getByText('3')).toBeInTheDocument();

    fireEvent.click(screen.getByRole('button', { name: '收起面板' }));
    expect(onClose).toHaveBeenCalledTimes(1);
  });

  it('hides the badge when zero or undefined', () => {
    const { container } = render(<PanelHeader title="图层" badge={0} />);
    expect(container.textContent).not.toContain('0');
  });
});

describe('EmptyState', () => {
  it('renders title and optional CTA action', () => {
    const onClick = vi.fn();
    render(
      <EmptyState
        icon={Layers}
        title="暂无图层"
        description="从数据源添加数据以创建图层"
        action={{ label: '前往数据源', onClick }}
      />
    );

    expect(screen.getByText('暂无图层')).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: '前往数据源' }));
    expect(onClick).toHaveBeenCalledTimes(1);
  });
});

describe('InlineNotice', () => {
  it('error uses role=alert, other variants use role=status', () => {
    const { unmount } = render(<InlineNotice variant="error">加载失败</InlineNotice>);
    expect(screen.getByRole('alert')).toHaveTextContent('加载失败');
    unmount();

    render(<InlineNotice variant="info">提示信息</InlineNotice>);
    expect(screen.getByRole('status')).toHaveTextContent('提示信息');
  });
});
