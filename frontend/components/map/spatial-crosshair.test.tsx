import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { render, fireEvent, act } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { SpatialCrosshair } from './spatial-crosshair';
import { useHudStore } from '@/lib/store/useHudStore';

describe('SpatialCrosshair', () => {
  beforeEach(() => {
    vi.useFakeTimers();
    useHudStore.setState({
      viewport: { center: [116.4074, 39.9042], zoom: 10, bearing: 0, pitch: 0 },
      aiStatus: 'idle',
      is3D: false,
    });
    // Mock navigator.clipboard
    Object.assign(navigator, {
      clipboard: {
        writeText: vi.fn().mockResolvedValue(undefined),
      },
    });
  });

  afterEach(() => {
    vi.useRealTimers();
  });

  it('renders correctly and copies coordinates on reticle click with timer cleanup', () => {
    const { container, unmount } = render(<SpatialCrosshair />);
    const svg = container.querySelector('svg');
    const dot = svg?.querySelector('circle');
    expect(svg).toBeDefined();
    expect(dot).toBeTruthy();

    if (svg && dot) {
      // 门控回归（spatial-crosshair.tsx:159 的 pointerEvents）：空闲态 24px
      // 装饰框不吃指针事件（不吞要素拾取），可复制态（thinking/copied）才
      // 'auto'。此前 fireEvent.click(svg) 绕过命中测试 —— 门控写错（恒
      // 'auto' 或恒 'none'）该测试仍绿，对门控零回归能力。
      expect(getComputedStyle(svg).pointerEvents).toBe('none');
      act(() => {
        useHudStore.setState({ aiStatus: 'thinking' });
      });
      expect(getComputedStyle(svg).pointerEvents).toBe('auto');
      act(() => {
        useHudStore.setState({ aiStatus: 'idle' });
      });
      expect(getComputedStyle(svg).pointerEvents).toBe('none');

      // 复制命中面 = 中心精确点（常驻 auto；死锁修复后空闲态可点）。
      act(() => {
        fireEvent.click(dot);
      });
      expect(navigator.clipboard.writeText).toHaveBeenCalledWith('116.407400, 39.904200');

      // Click again to test timer reset
      act(() => {
        fireEvent.click(dot);
      });

      // Fast-forward timers
      act(() => {
        vi.advanceTimersByTime(1500);
      });
    }

    // Unmount to verify no errors from dangling timers
    unmount();
  });

  it('空闲态点击准星中心点即可复制坐标（pointer-events 门控不死锁）', async () => {
    // user-event 的直接 API 走真实指针路径并校验 pointer-events（fireEvent 会
    // 绕过门控，正是此前该缺陷被测试掩盖的原因）；直接 API 不替换剪贴板桩，
    // beforeEach 的 writeText spy 保持可断言。本环境 user-event 与 fake
    // timers 不兼容（内部 wait 永不结算），用例内切回真实计时器。
    vi.useRealTimers();
    const { container } = render(<SpatialCrosshair />);
    const svg = container.querySelector('svg');
    const dot = svg?.querySelector('circle');
    expect(svg).toBeTruthy();
    expect(dot).toBeTruthy();

    // 审计契约：空闲态 24px 装饰框仍 pointer-events:none（不吞要素拾取）。
    expect(getComputedStyle(svg as Element).pointerEvents).toBe('none');
    // 死锁修复：复制命中面收敛到中心精确点（常驻 auto）。
    expect(getComputedStyle(dot as Element).pointerEvents).toBe('auto');

    // 未修复代码：dot 继承 svg 的 'none' → 上面的样式断言先失败；
    // 即使绕过断言，user-event 也会因 pointer-events 拒绝点击。
    await userEvent.click(dot as Element);
    expect(navigator.clipboard.writeText).toHaveBeenCalledWith('116.407400, 39.904200');
    // 复制反馈出现（aria-label 翻转）。
    expect(svg!.getAttribute('aria-label')).toBe('坐标已复制');
  });

  it('剪贴板写入拒绝时不谎报「已复制」，也不产生未处理拒绝', async () => {
    vi.useRealTimers();
    Object.assign(navigator, {
      clipboard: { writeText: vi.fn().mockRejectedValue(new Error('denied')) },
    });
    const { container } = render(<SpatialCrosshair />);
    const dot = container.querySelector('svg circle');
    expect(dot).toBeTruthy();
    await userEvent.click(dot as Element);
    const svg = container.querySelector('svg');
    expect(svg!.getAttribute('aria-label')).toBe('复制当前地图中心坐标');
  });
});
