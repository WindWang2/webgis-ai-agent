/**
 * #807: ChartRenderer 主题重派生 —— 外层 ChatMessageItem 是 memo 边界
 * （stable props），主题切换不会跨过它；此前已渲染图表的 tick/tooltip 色
 * 会滞留旧主题直到下一条消息更新。修复：各 Render* 子组件自订阅
 * useHudStore 的 theme，toggle 时重派生。
 *
 * recharts 以 mock 面替换（把 tick fill 投影到 DOM）—— jsdom 下真实
 * recharts 渲染依赖容器尺寸，不稳定。
 */
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, act } from '@testing-library/react';

const themeState = { theme: 'light' as string };
const themeListeners = new Set<() => void>();
const setTheme = (t: string) => {
  themeState.theme = t;
  themeListeners.forEach((l) => l());
};

vi.mock('@/lib/store/useHudStore', async () => {
  const React = await import('react');
  const { useSyncExternalStore } = React;
  const subscribe = (cb: () => void) => {
    themeListeners.add(cb);
    return () => themeListeners.delete(cb);
  };
  const useHudStore = (selector: (s: { theme: string }) => unknown) =>
    useSyncExternalStore(subscribe, () => selector(themeState), () => selector(themeState));
  useHudStore.getState = () => themeState;
  return { useHudStore };
});

let lastTickFill = '';
vi.mock('recharts', async () => {
  const React = await import('react');
  const passthrough = ({ children }: any) => React.createElement('div', null, children);
  return {
    ResponsiveContainer: passthrough,
    BarChart: passthrough,
    LineChart: passthrough,
    PieChart: passthrough,
    ScatterChart: passthrough,
    CartesianGrid: passthrough,
    XAxis: ({ tick }: any) => {
      lastTickFill = tick?.fill ?? '';
      return React.createElement('div');
    },
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

// ChartRenderer 经测试体内 await import('./chart-renderer') 动态加载,
// 以便 vi.mock 工厂先行就位。

// 让 themeColor 读到可控的 computed token
const setToken = (name: string, value: string) => {
  window.document.documentElement.style.setProperty(name, value);
};

describe('ChartRenderer theme re-derivation (#807)', () => {
  beforeEach(() => {
    themeState.theme = 'light';
    setToken('--text-muted', 'rgb(91, 107, 130)');
  });

  it('主题切换后（无 memo 边界外的重渲染）tick fill 重派生为新 token', async () => {
    const { ChartRenderer: CR } = await import('./chart-renderer');
    render(<CR chart={{ type: 'bar', title: 't', data: [{ name: 'a', value: 1 }] } as any} />);
    expect(lastTickFill).toBe('rgb(91, 107, 130)');

    // 切换主题 + token（模拟 dark 主题的 --text-muted）
    setToken('--text-muted', 'rgb(148, 163, 184)');
    act(() => setTheme('dark'));

    expect(lastTickFill).toBe('rgb(148, 163, 184)');
  });
});
