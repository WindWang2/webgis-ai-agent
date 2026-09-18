/**
 * StoryView 编排模式集成测试（ADR-0196）。
 *
 * 后端 /storymap/compile 命中时：编排徽标出现、spec 章节经 StoryNarrator
 * 渲染、相机命令升级为全参 fly_to（pitch/bearing）、联动看板挂载。
 * 编译失败 / 形状不合法必须静默回退本地派生；会话切换必须清除编排残留；
 * immersive 排版不得被 position 类冲突吃掉。
 */
import { beforeEach, afterEach, describe, expect, it, vi } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { StoryView } from './story-view';
import { useHudStore } from '@/lib/store/useHudStore';

// 可变会话 id：会话切换用例改写它后再 rerender。
let mockSessionId = 'spec-1';
vi.mock('next/navigation', () => ({
  useSearchParams: () => new URLSearchParams(`session_id=${mockSessionId}`),
}));

vi.mock('@/components/map/map-panel', () => ({
  MapPanel: () => <div data-testid="map-panel" />,
}));
vi.mock('@/components/chat/story-markdown', () => ({
  default: ({ text }: { text: string }) => <div data-testid="story-md">{text}</div>,
}));
vi.mock('react-map-gl/maplibre', () => ({ useMap: () => ({}) }));

const dispatchActionMock = vi.fn();
vi.mock('@/lib/contexts/map-action-context', () => ({
  useMapAction: () => ({ dispatchAction: dispatchActionMock }),
}));
vi.mock('@/lib/utils/logger', () => ({
  devOnly: { log: vi.fn(), warn: vi.fn(), error: vi.fn() },
  safeError: vi.fn(),
}));
vi.mock('@/lib/api/config', () => ({ API_BASE: 'http://localhost:8000' }));
vi.mock('@/components/chat/chart-core', () => ({
  ChartCore: ({ chart }: { chart: { kind?: string } }) => (
    <div data-testid="chart-core">{chart?.kind ?? 'chart'}</div>
  ),
}));
vi.mock('@/lib/chart-adapter', () => ({
  adaptChartData: (raw: unknown) => raw,
}));

const jsonOk = (body: unknown) => ({
  ok: true,
  status: 200,
  statusText: 'OK',
  text: () => Promise.resolve(JSON.stringify(body)),
});

const SPEC = {
  schema_version: '1.0',
  metadata: { title: '北京热岛专报', summary: '建议屋顶绿化改造' },
  chapters: [
    { id: 'arc-introduction', title: '引言', narrative: '分析目标正文', arc_role: 'introduction',
      linked_widget_ids: [], duration_hint_s: 3 },
    { id: 'arc-macro_situation', title: '宏观态势', narrative: '全局分布正文', arc_role: 'macro_situation',
      linked_widget_ids: ['w-chart-1'], duration_hint_s: 4 },
  ],
  camera_keyframes: [
    { chapter_id: 'arc-introduction', t: 0, center: [116.4, 39.9], zoom: 5, pitch: 18, bearing: 0 },
    { chapter_id: 'arc-macro_situation', t: 0, center: [116.2, 39.8], zoom: 11, pitch: 10, bearing: 0 },
  ],
  linked_widgets: [
    { id: 'w-chart-1', kind: 'chart', ref: 'ref:chart-heat-1', title: '热岛强度', chapter_id: 'arc-macro_situation',
      data: { kind: 'bar' } },
  ],
};

const MESSAGES = [
  { id: 'm1', role: 'user', content: '分析北京热岛' },
  { id: 'm2', role: 'assistant', content: '## 结论\n热岛显著' },
];

const fetchMock = vi.fn();

beforeEach(() => {
  vi.clearAllMocks();
  fetchMock.mockReset();
  mockSessionId = 'spec-1';
  vi.stubGlobal('fetch', fetchMock);
  useHudStore.getState().clearLayers();
});

afterEach(() => {
  vi.unstubAllGlobals();
});

describe('StoryView 编排模式（ADR-0196）', () => {
  it('spec 命中：编排徽标 + spec 章节渲染 + 全参 fly_to + 看板挂载', async () => {
    fetchMock
      .mockResolvedValueOnce(jsonOk({ messages: MESSAGES }))
      .mockResolvedValueOnce(jsonOk({ map_state: null }))
      .mockResolvedValueOnce(jsonOk(SPEC));

    render(<StoryView />);

    // spec 章节经 StoryNarrator 渲染（编排徽标是模式切换的可见证据）
    expect(await screen.findByTestId('story-orchestrated')).toBeInTheDocument();
    expect(screen.getByText(/宏观态势/)).toBeInTheDocument();
    // 章节总数走 spec 序列（2 章），scrubber 收缩
    expect(screen.getByText('1/2')).toBeInTheDocument();

    // 相机命令升级为全参 fly_to（含 pitch/bearing）
    await waitFor(() => {
      expect(dispatchActionMock).toHaveBeenCalledWith({
        command: 'fly_to',
        params: { center: [116.4, 39.9], zoom: 5, pitch: 18, bearing: 0 },
      });
    });
    expect(screen.getByTestId('story-dashboard-mount')).toBeInTheDocument();
  });

  it('compile 返回垃圾形状：静默回退本地派生（无徽标、消息章节照常）', async () => {
    fetchMock
      .mockResolvedValueOnce(jsonOk({ messages: MESSAGES }))
      .mockResolvedValueOnce(jsonOk({ map_state: null }))
      .mockResolvedValueOnce(jsonOk({ messages: MESSAGES })); // 假 spec：无 chapters

    render(<StoryView />);

    expect(await screen.findByTestId('story-md')).toBeInTheDocument();
    expect(screen.queryByTestId('story-orchestrated')).toBeNull();
    // 本地派生：2 条消息 = 2 章节
    expect(document.querySelectorAll('[data-story-chapter]')).toHaveLength(2);
  });

  it('spec 深层缺字段（keyframe 空对象）：门卫拦截，不崩且回退本地派生', async () => {
    const brokenSpec = {
      schema_version: '1.0',
      chapters: [{ id: 'arc-x', title: 'X', narrative: 'n', arc_role: 'introduction' }],
      camera_keyframes: [{}], // 旧门卫放行 → specToNarratorView 解引用崩溃
    };
    fetchMock
      .mockResolvedValueOnce(jsonOk({ messages: MESSAGES }))
      .mockResolvedValueOnce(jsonOk({ map_state: null }))
      .mockResolvedValueOnce(jsonOk(brokenSpec));

    render(<StoryView />);

    // 页面照常渲染（无渲染期异常），且不进入编排模式
    expect(await screen.findByTestId('story-md')).toBeInTheDocument();
    await waitFor(() => {
      expect(screen.queryByTestId('story-orchestrated')).toBeNull();
    });
    expect(document.querySelectorAll('[data-story-chapter]')).toHaveLength(2);
  });

  it('#1370：用户先 seek 后 spec 到达，相机不被拉回 spec 第 0 章', async () => {
    let resolveCompile: ((value: unknown) => void) | undefined;
    const compilePending = new Promise((resolve) => {
      resolveCompile = resolve;
    });
    fetchMock
      .mockResolvedValueOnce(jsonOk({ messages: MESSAGES }))
      .mockResolvedValueOnce(jsonOk({ map_state: null }))
      .mockImplementationOnce(() => compilePending);

    const user = userEvent.setup();
    render(<StoryView />);
    expect(await screen.findByTestId('story-md')).toBeInTheDocument();

    await user.click(screen.getByRole('button', { name: '下一章节' }));
    dispatchActionMock.mockClear();
    resolveCompile?.(jsonOk(SPEC));

    expect(await screen.findByTestId('story-orchestrated')).toBeInTheDocument();
    await waitFor(() => {
      expect(screen.getByText(/宏观态势/)).toBeInTheDocument();
    });
    expect(dispatchActionMock).not.toHaveBeenCalledWith({
      command: 'fly_to',
      params: { center: [116.4, 39.9], zoom: 5, pitch: 18, bearing: 0 },
    });
  });

  it('#1370：用户先播放后 spec 到达，播放位置不被迟到 spec 覆盖', async () => {
    let resolveCompile: ((value: unknown) => void) | undefined;
    const compilePending = new Promise((resolve) => {
      resolveCompile = resolve;
    });
    fetchMock
      .mockResolvedValueOnce(jsonOk({ messages: MESSAGES }))
      .mockResolvedValueOnce(jsonOk({ map_state: null }))
      .mockImplementationOnce(() => compilePending);

    const user = userEvent.setup();
    render(<StoryView />);
    expect(await screen.findByTestId('story-md')).toBeInTheDocument();

    await user.click(screen.getByRole('button', { name: '播放' }));
    dispatchActionMock.mockClear();
    resolveCompile?.(jsonOk(SPEC));

    expect(await screen.findByTestId('story-orchestrated')).toBeInTheDocument();
    expect(dispatchActionMock).not.toHaveBeenCalledWith({
      command: 'fly_to',
      params: { center: [116.4, 39.9], zoom: 5, pitch: 18, bearing: 0 },
    });
  });

  it('immersive 排版：面板 position 不与 relative 冲突，看板为浮层让位', async () => {
    fetchMock
      .mockResolvedValueOnce(jsonOk({ messages: MESSAGES }))
      .mockResolvedValueOnce(jsonOk({ map_state: null }))
      .mockResolvedValueOnce(jsonOk(SPEC));

    const user = userEvent.setup();
    render(<StoryView />);
    await screen.findByTestId('story-orchestrated');

    const panel = screen.getByTestId('story-narrative-panel');
    const mapBox = screen.getByTestId('story-map-container');
    // split 模式：面板贴左、地图相对布局
    expect(panel.className).toMatch(/\brelative\b/);
    expect(panel.className).not.toMatch(/\babsolute\b/);

    await user.click(screen.getByRole('button', { name: '沉浸模式' }));

    // immersive：panel/map 的 position 必须互斥（旧实现 relative+absolute 并存被 CSS 顺序判负）
    expect(panel.className).toMatch(/\babsolute\b/);
    expect(panel.className).not.toMatch(/\brelative\b/);
    expect(mapBox.className).toMatch(/\babsolute\b/);
    expect(mapBox.className).not.toMatch(/\brelative\b/);
    // 看板给右浮面板让位（calc 偏移），否则被面板完全盖住
    expect(screen.getByTestId('story-dashboard-mount').className).toContain('calc(');
  });

  it('会话切换：编排 spec 与残留状态清除，回到本地派生', async () => {
    fetchMock
      .mockResolvedValueOnce(jsonOk({ messages: MESSAGES }))
      .mockResolvedValueOnce(jsonOk({ map_state: null }))
      .mockResolvedValueOnce(jsonOk(SPEC));

    const { rerender } = render(<StoryView />);
    await screen.findByTestId('story-orchestrated');
    expect(screen.getByTestId('story-dashboard-mount')).toBeInTheDocument();

    // 切到新会话：compile 返回垃圾 → 本地派生；旧 spec 必须被清除
    mockSessionId = 'spec-2';
    fetchMock
      .mockResolvedValueOnce(jsonOk({ messages: [{ id: 'n1', role: 'assistant', content: '新会话正文' }] }))
      .mockResolvedValueOnce(jsonOk({ map_state: null }))
      .mockResolvedValueOnce(jsonOk({}));
    rerender(<StoryView />);

    expect(await screen.findByText('新会话正文')).toBeInTheDocument();
    await waitFor(() => {
      expect(screen.queryByTestId('story-orchestrated')).toBeNull();
    });
    expect(screen.queryByTestId('story-dashboard-mount')).toBeNull();
    expect(document.querySelectorAll('[data-story-chapter]')).toHaveLength(1);
  });
});
