/**
 * StoryNarrator / StoryDashboard 组件测试（ADR-0196）。
 *
 * 验收面：滚动驱动（Scroll-driven）镜头移动平滑响应并同步触发图表高亮。
 * - 滚动位置 → onActiveChange（rAF 节流、同章去重）→ 父层 fly_to 全参
 *   （center/zoom/pitch/bearing）派发恰好一次；
 * - 活跃章节的联动 widget → StoryDashboard 高亮（脉冲环）；
 * - 外部 activeId 变更（scrubber/播放）→ scrollIntoView 程序化滚动，
 *   reduced-motion 降级为 'auto'。
 */
import { beforeEach, describe, expect, it, vi, type Mock } from 'vitest';
import { act, fireEvent, render, screen } from '@testing-library/react';
import React, { useState } from 'react';

import { StoryNarrator, pickActiveChapter, type NarratorChapter } from './story-narrator';
import { StoryDashboard, type DashboardWidget } from './story-dashboard';

vi.mock('@/components/chat/chart-core', () => ({
  ChartCore: ({ chart }: { chart: { kind?: string } }) => (
    <div data-testid="chart-core">{chart?.kind ?? 'chart'}</div>
  ),
}));
vi.mock('@/lib/chart-adapter', () => ({
  adaptChartData: (raw: unknown) => raw,
}));
vi.mock('@/lib/utils/logger', () => ({
  devOnly: { log: vi.fn(), warn: vi.fn(), error: vi.fn() },
  safeError: vi.fn(),
}));

const CHAPTERS: NarratorChapter[] = [
  {
    id: 'arc-introduction',
    title: '引言',
    text: '分析目标正文',
    arcRole: 'introduction',
    camera: { center: [116.4, 39.9], zoom: 5, pitch: 18, bearing: 0 },
  },
  {
    id: 'arc-macro_situation',
    title: '宏观态势',
    text: '全局分布正文',
    arcRole: 'macro_situation',
    camera: { center: [116.2, 39.8], zoom: 11, pitch: 10, bearing: 0 },
    widgetIds: ['w-chart-1'],
  },
  {
    id: 'arc-focus_dissection',
    title: '重点解剖',
    text: '热点解剖正文',
    arcRole: 'focus_dissection',
    camera: { center: [116.45, 39.9], zoom: 14, pitch: 45, bearing: 15 },
    widgetIds: ['w-kpi-2'],
  },
];

const WIDGETS: DashboardWidget[] = [
  { id: 'w-chart-1', kind: 'chart', title: '热岛强度对比', data: { kind: 'bar' } },
  { id: 'w-kpi-2', kind: 'kpi', title: '热岛强度峰值', data: { value: 3.1 } },
];

/** 模拟故事视角的父层：活跃章节变更 → fly_to 全参 + 高亮联动。 */
function makeHarness(opts: {
  onDispatch: Mock;
  initialActive?: string | null;
}) {
  function Harness(): React.ReactElement {
    const [activeId, setActiveId] = useState<string | null>(
      opts.initialActive ?? CHAPTERS[0].id,
    );
    const [highlighted, setHighlighted] = useState<string[]>([]);
    const handleActive = (id: string): void => {
      setActiveId(id);
      const ch = CHAPTERS.find((c) => c.id === id);
      if (ch?.camera) {
        opts.onDispatch({
          command: 'fly_to',
          params: {
            center: ch.camera.center,
            ...(ch.camera.zoom !== undefined ? { zoom: ch.camera.zoom } : {}),
            ...(ch.camera.pitch !== undefined ? { pitch: ch.camera.pitch } : {}),
            ...(ch.camera.bearing !== undefined ? { bearing: ch.camera.bearing } : {}),
          },
        });
      }
      setHighlighted(ch?.widgetIds ?? []);
    };
    return (
      <div>
        <StoryNarrator
          chapters={CHAPTERS}
          activeId={activeId}
          onActiveChange={handleActive}
          renderBody={(ch) => <div data-testid={`body-${ch.id}`}>{ch.text}</div>}
        />
        <StoryDashboard widgets={WIDGETS} highlightedIds={highlighted} />
      </div>
    );
  }
  return Harness;
}

/** jsdom 无布局：以受控 mock 矩形模拟「滚动到 scrollY」后的几何。 */
function mockScrollGeometry(scrollY: number): HTMLElement {
  const container = document.querySelector<HTMLElement>('[data-story-narrator]');
  if (!container) throw new Error('narrator container missing');
  vi.spyOn(container, 'getBoundingClientRect').mockReturnValue({
    top: 0, height: 120, bottom: 120, width: 400, left: 0, right: 400,
    x: 0, y: 0, toJSON: () => ({}),
  } as DOMRect);
  const articles = Array.from(
    container.querySelectorAll<HTMLElement>('[data-story-chapter]'),
  );
  articles.forEach((el, i) => {
    vi.spyOn(el, 'getBoundingClientRect').mockReturnValue({
      top: i * 100 - scrollY, bottom: i * 100 + 100 - scrollY,
      height: 100, width: 400, left: 0, right: 400, x: 0, y: 0,
      toJSON: () => ({}),
    } as DOMRect);
  });
  return container;
}

/** jsdom 的 rAF 是 ~16ms 定时器实现：滚动后冲刷一帧再断言。 */
async function scrollAndFlush(container: HTMLElement): Promise<void> {
  await act(async () => {
    fireEvent.scroll(container);
    await new Promise((r) => setTimeout(r, 30));
  });
}

const scrollIntoViewMock = vi.fn();

beforeEach(() => {
  vi.clearAllMocks();
  Element.prototype.scrollIntoView = scrollIntoViewMock;
});

describe('pickActiveChapter（活跃章判定纯函数）', () => {
  it('取最后一个越过激活线（容器高 40%）的章节', () => {
    expect(pickActiveChapter([0, 100, 200], 48)).toBe(0);
    expect(pickActiveChapter([-60, 40, 140], 48)).toBe(1);
    expect(pickActiveChapter([-160, -60, 40], 48)).toBe(2);
  });

  it('全部在激活线下方 / 空列表的退化', () => {
    expect(pickActiveChapter([100, 200], 48)).toBe(-1);
    expect(pickActiveChapter([], 48)).toBe(-1);
  });
});

describe('StoryNarrator 滚动驱动镜头同步', () => {
  it('渲染章节正文并标记活跃章', () => {
    const Harness = makeHarness({ onDispatch: vi.fn() });
    const { container } = render(<Harness />);
    expect(screen.getByTestId('body-arc-introduction')).toBeInTheDocument();
    expect(screen.getByTestId('body-arc-focus_dissection')).toBeInTheDocument();
    expect(
      container.querySelector('[data-story-active="true"]')?.getAttribute('data-story-chapter'),
    ).toBe('arc-introduction');
  });

  it('滚动 → onActiveChange → fly_to 携带 pitch/bearing 全参派发恰好一次', async () => {
    const onDispatch = vi.fn();
    const Harness = makeHarness({ onDispatch });
    render(<Harness />);

    // 初始位置（第 0 章活跃）：滚动事件不产生新派发
    let container = mockScrollGeometry(0);
    await scrollAndFlush(container);
    expect(onDispatch).not.toHaveBeenCalled();

    // 滚过第 1 章（top=40-120=-80 ≤ 激活线 48）：全参 fly_to 恰好一次
    container = mockScrollGeometry(120);
    await scrollAndFlush(container);
    expect(onDispatch).toHaveBeenCalledTimes(1);
    expect(onDispatch).toHaveBeenCalledWith({
      command: 'fly_to',
      params: { center: [116.2, 39.8], zoom: 11, pitch: 10, bearing: 0 },
    });

    // 同章内继续滚动：同 id 去重，不重复派发
    container = mockScrollGeometry(150);
    await scrollAndFlush(container);
    expect(onDispatch).toHaveBeenCalledTimes(1);
  });

  it('连续滚过两章：镜头分步平滑跟进（每章一次派发）', async () => {
    const onDispatch = vi.fn();
    const Harness = makeHarness({ onDispatch });
    render(<Harness />);
    await scrollAndFlush(mockScrollGeometry(120));
    await scrollAndFlush(mockScrollGeometry(230));
    expect(onDispatch).toHaveBeenCalledTimes(2);
    expect(onDispatch).toHaveBeenLastCalledWith({
      command: 'fly_to',
      params: { center: [116.45, 39.9], zoom: 14, pitch: 45, bearing: 15 },
    });
  });

  it('滚动驱动联动图表高亮同步（活跃章节的 widget 脉冲）', async () => {
    const onDispatch = vi.fn();
    const Harness = makeHarness({ onDispatch });
    render(<Harness />);
    expect(
      screen.getByTestId('story-widget-w-kpi-2').getAttribute('data-story-widget-highlight'),
    ).toBeNull();

    await scrollAndFlush(mockScrollGeometry(230)); // → 第 3 章（含 w-kpi-2）
    expect(screen.getByTestId('story-widget-w-kpi-2')).toHaveAttribute(
      'data-story-widget-highlight', 'true',
    );
    expect(screen.getByTestId('story-widget-w-chart-1').getAttribute('data-story-widget-highlight')).toBeNull();
  });
});

describe('StoryNarrator 外部定位与 reduced-motion', () => {
  it('外部 activeId 变更 → scrollIntoView 平滑定位', () => {
    const { rerender } = render(
      <StoryNarrator chapters={CHAPTERS} activeId="arc-introduction"
        onActiveChange={() => {}} />,
    );
    rerender(
      <StoryNarrator chapters={CHAPTERS} activeId="arc-focus_dissection"
        onActiveChange={() => {}} />,
    );
    expect(Element.prototype.scrollIntoView).toHaveBeenCalledWith(
      expect.objectContaining({ behavior: 'smooth', block: 'start' }),
    );
  });

  it('prefers-reduced-motion → 定位降级为 auto', () => {
    const mql = { matches: true, addEventListener: vi.fn(), removeEventListener: vi.fn() };
    vi.stubGlobal('matchMedia', vi.fn().mockReturnValue(mql));
    try {
      const { rerender } = render(
        <StoryNarrator chapters={CHAPTERS} activeId="arc-introduction"
          onActiveChange={() => {}} />,
      );
      vi.mocked(scrollIntoViewMock).mockClear();
      rerender(
        <StoryNarrator chapters={CHAPTERS} activeId="arc-macro_situation"
          onActiveChange={() => {}} />,
      );
      expect(scrollIntoViewMock).toHaveBeenCalledWith(
        expect.objectContaining({ behavior: 'auto' }),
      );
    } finally {
      vi.unstubAllGlobals();
    }
  });
});

describe('StoryDashboard', () => {
  it('无联动 widget 时不渲染', () => {
    const { container } = render(<StoryDashboard widgets={[]} highlightedIds={[]} />);
    expect(container.querySelector('[data-testid="story-dashboard"]')).toBeNull();
  });

  it('chart kind 渲染图表核，kpi 渲染数值', () => {
    render(<StoryDashboard widgets={WIDGETS} highlightedIds={['w-chart-1']} />);
    expect(screen.getByTestId('chart-core')).toBeInTheDocument();
    expect(screen.getByTestId('story-widget-w-chart-1')).toHaveAttribute(
      'data-story-widget-highlight', 'true',
    );
  });
});
