import { beforeEach, describe, expect, it, vi } from 'vitest';
import { act, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { OnboardingRoot } from './onboarding-root';
import { HintQueue } from './hint-queue';
import { Tour } from './tour';
import { loadPersist, useOnboardingStore } from '@/lib/onboarding/use-onboarding';
import { HINTS, TOUR_STEPS } from './tour-steps';

const storage = new Map<string, string>();
beforeEach(() => {
  storage.clear();
  vi.mocked(localStorage.getItem).mockImplementation((k: string) => storage.get(k) ?? null);
  vi.mocked(localStorage.setItem).mockImplementation((k: string, v: string) => {
    storage.set(k, v);
  });
  act(() => {
    useOnboardingStore.setState({
      tourOpen: false,
      stepIndex: 0,
      tourSeen: true, // 默认不自动弹（首跑场景单独测）
      hintsSeen: HINTS.map((h) => h.id),
      activeHintId: null,
    });
  });
});

describe('Tour', () => {
  it('步骤推进：下一步 → 完成 → tourSeen 落盘；Esc 跳过等价完成', () => {
    render(<Tour />);
    act(() => useOnboardingStore.getState().startTour());
    expect(screen.getByTestId('tour-card')).toHaveTextContent('第 1 / 10 步');
    fireEvent.click(screen.getByRole('button', { name: '下一步' }));
    expect(screen.getByTestId('tour-card')).toHaveTextContent('第 2 / 10 步');
    fireEvent.keyDown(screen.getByTestId('onboarding-tour'), { key: 'Escape' });
    expect(useOnboardingStore.getState().tourOpen).toBe(false);
    expect(useOnboardingStore.getState().tourSeen).toBe(true);
    expect(loadPersist().tourSeen).toBe(true);
  });

  it('「下一步」走完最后一步自动完成；无目标步骤降级为居中卡片', () => {
    render(<Tour />);
    act(() => {
      useOnboardingStore.setState({ tourOpen: true, stepIndex: TOUR_STEPS.length - 1 });
    });
    // done 步无 targetSelector → 无 spotlight（居中卡片）
    expect(screen.queryByTestId('tour-spotlight')).toBeNull();
    fireEvent.click(screen.getByRole('button', { name: '完成' }));
    expect(useOnboardingStore.getState().tourOpen).toBe(false);
  });

  it('有目标步骤渲染焦点圈闭（rail 步锚定 nav）', async () => {
    const nav = document.createElement('nav');
    // jsdom 无布局：直接锚定测量值
    nav.getBoundingClientRect = () =>
      ({ top: 10, left: 10, right: 110, bottom: 50, width: 100, height: 40, x: 10, y: 10, toJSON: () => ({}) }) as DOMRect;
    document.body.appendChild(nav);
    try {
      render(<Tour />);
      act(() => {
        useOnboardingStore.setState({ tourOpen: true, stepIndex: 3 }); // rail 步
      });
      await waitFor(() => expect(screen.getByTestId('tour-spotlight')).toBeInTheDocument());
      // jsdom 默认非 reduce → 保留脉冲动画类
      expect(screen.getByTestId('tour-spotlight').className).toContain('animate-pulse');
    } finally {
      nav.remove();
    }
  });

  it('reduced-motion 降级路径：无脉冲动画（双轨约定的 JS 侧）', async () => {
    const nav = document.createElement('nav');
    nav.getBoundingClientRect = () =>
      ({ top: 10, left: 10, right: 110, bottom: 50, width: 100, height: 40, x: 10, y: 10, toJSON: () => ({}) }) as DOMRect;
    document.body.appendChild(nav);
    vi.stubGlobal(
      'matchMedia',
      vi.fn().mockReturnValue({
        matches: true,
        addEventListener: vi.fn(),
        removeEventListener: vi.fn(),
        addListener: vi.fn(),
        removeListener: vi.fn(),
      }),
    );
    try {
      render(<Tour />);
      act(() => {
        useOnboardingStore.setState({ tourOpen: true, stepIndex: 3 });
      });
      await waitFor(() => expect(screen.getByTestId('tour-spotlight')).toBeInTheDocument());
      expect(screen.getByTestId('tour-spotlight').className).not.toContain('animate-pulse');
    } finally {
      nav.remove();
      vi.unstubAllGlobals();
    }
  });

  it('步骤完整性：10 步、每步有标题与正文', () => {
    expect(TOUR_STEPS).toHaveLength(10);
    for (const step of TOUR_STEPS) {
      expect(step.title.length).toBeGreaterThan(0);
      expect(step.body.length).toBeGreaterThan(0);
    }
  });
});

describe('OnboardingRoot 首次运行触发', () => {
  it('tourSeen=false 时延迟自动开启 tour', async () => {
    vi.useFakeTimers();
    try {
      act(() => {
        useOnboardingStore.setState({ tourSeen: false, tourOpen: false });
      });
      render(<OnboardingRoot />);
      await vi.advanceTimersByTimeAsync(1300);
      expect(useOnboardingStore.getState().tourOpen).toBe(true);
    } finally {
      vi.useRealTimers();
    }
  });

  it('已看过则不弹', () => {
    render(<OnboardingRoot />);
    expect(useOnboardingStore.getState().tourOpen).toBe(false);
  });
});

describe('HintQueue', () => {
  it('未看完 tour 不展示；每条只出现一次；关闭后轮到下一条', async () => {
    vi.useFakeTimers();
    try {
      act(() => {
        useOnboardingStore.setState({ tourSeen: false, activeHintId: null });
      });
      const { rerender } = render(<HintQueue />);
      expect(screen.queryByTestId('onboarding-hint')).toBeNull();

      act(() => useOnboardingStore.setState({ tourSeen: true, hintsSeen: [] }));
      rerender(<HintQueue />);
      // 1.5s 后出第一条
      await vi.advanceTimersByTimeAsync(1600);
      expect(screen.getByTestId('onboarding-hint')).toBeInTheDocument();
      const first = useOnboardingStore.getState().activeHintId;
      expect(first).toBe(HINTS[0].id);

      // 手动「下一条」= 标记已读 → 换下一条
      fireEvent.click(screen.getByRole('button', { name: '下一条' }));
      await vi.advanceTimersByTimeAsync(1600);
      expect(useOnboardingStore.getState().activeHintId).toBe(HINTS[1].id);
      expect(useOnboardingStore.getState().hintsSeen).toContain(HINTS[0].id);
    } finally {
      vi.useRealTimers();
    }
  });

  it('全部提示已读后静默', () => {
    act(() => {
      useOnboardingStore.setState({ tourSeen: true, hintsSeen: HINTS.map((h) => h.id), activeHintId: null });
    });
    render(<HintQueue />);
    expect(screen.queryByTestId('onboarding-hint')).toBeNull();
  });

  it('重置提示：settings 入口 → resetHints 清空已读集', () => {
    act(() => useOnboardingStore.setState({ hintsSeen: HINTS.map((h) => h.id) }));
    act(() => useOnboardingStore.getState().resetHints());
    expect(useOnboardingStore.getState().hintsSeen).toEqual([]);
    expect(loadPersist().hintsSeen).toEqual([]);
  });
});
