import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { useHudStore } from '@/lib/store/useHudStore';
import { applyStoryMapState } from '@/lib/session/map-state-restore';

// next/navigation：固定回放 session。
vi.mock('next/navigation', () => ({
  useSearchParams: () => new URLSearchParams('session_id=s1'),
}));

// dynamic() 目标模块直接替换成轻量桩，避免拉入 maplibre。
vi.mock('@/components/map/map-panel', () => ({
  MapPanel: () => <div data-testid="map-panel" />,
}));
vi.mock('@/components/chat/story-markdown', () => ({
  default: ({ text }: { text: string }) => <div data-testid="story-md">{text}</div>,
}));

const dispatchActionMock = vi.fn();
vi.mock('@/lib/contexts/map-action-context', () => ({
  useMapAction: () => ({ dispatchAction: dispatchActionMock }),
}));

vi.mock('@/lib/utils/logger', () => ({
  devOnly: { log: vi.fn(), warn: vi.fn(), error: vi.fn() },
  safeError: vi.fn(),
}));
vi.mock('@/lib/api/config', () => ({ API_BASE: 'http://localhost:8000' }));

import StoryPage from './page';

const jsonOk = (body: unknown, status = 200) => ({
  ok: true,
  status,
  statusText: 'OK',
  text: () => Promise.resolve(typeof body === 'string' ? body : JSON.stringify(body)),
});

const jsonErr = (status: number, statusText: string, body: unknown) => ({
  ok: false,
  status,
  statusText,
  text: () => Promise.resolve(typeof body === 'string' ? body : JSON.stringify(body)),
});

const fetchMock = vi.fn();

beforeEach(() => {
  vi.clearAllMocks();
  fetchMock.mockReset();
  vi.stubGlobal('fetch', fetchMock);
  useHudStore.getState().clearLayers();
  useHudStore.setState({ baseLayer: 'Carto 深色' });
  Object.defineProperty(navigator, 'clipboard', {
    configurable: true,
    value: { writeText: vi.fn().mockResolvedValue(undefined) },
  });
});

afterEach(() => {
  vi.unstubAllGlobals();
});

describe('StoryPage (#552)', () => {
  it('renders an explicit error state instead of a silent blank on restore failure', async () => {
    fetchMock
      .mockResolvedValueOnce(jsonErr(404, 'Not Found', { detail: 'session not found' }));

    render(<StoryPage />);

    // 之前：ApiError 被吞 → 空消息 + 无任何提示；现在必须出现可见错误态。
    await screen.findByRole('alert');
    expect(screen.getByText('无法加载该会话')).toBeInTheDocument();
    expect(screen.getByText(/session not found/)).toBeInTheDocument();
    expect(screen.getByText(/匿名会话暂不支持跨页面分享/)).toBeInTheDocument();
  });

  it('renders restored messages and applies the session map-state', async () => {
    fetchMock
      .mockResolvedValueOnce(jsonOk({ messages: [{ id: 'm1', role: 'assistant', content: '叙事正文' }] }))
      .mockResolvedValueOnce(jsonOk({
        map_state: {
          viewport: { center: [116, 39], zoom: 12 },
          mapspec: { view: { center: [116, 39], zoom: 12, framed: true } },
        },
      }));

    render(<StoryPage />);

    await screen.findByText('叙事正文');
    await waitFor(() => {
      expect(dispatchActionMock).toHaveBeenCalledWith({
        command: 'fly_to',
        params: expect.objectContaining({ center: [116, 39], zoom: 12 }),
      });
    });
  });

  it('renders the map layers from a restored map-state (shared content)', async () => {
    fetchMock
      .mockResolvedValueOnce(jsonOk({ messages: [] }))
      .mockResolvedValueOnce(jsonOk({ map_state: { layers: [{ id: 'L1', name: 'A', type: 'vector', visible: true, opacity: 1, source: { type: 'FeatureCollection', features: [] } }] } }));

    render(<StoryPage />);

    await waitFor(() => {
      expect(useHudStore.getState().layers).toHaveLength(1);
      expect(useHudStore.getState().layers[0].id).toBe('L1');
    });
  });

  it('wires the share button to copy the current URL', async () => {
    fetchMock
      .mockResolvedValueOnce(jsonOk({ messages: [] }))
      .mockResolvedValueOnce(jsonOk({ map_state: null }));

    const user = userEvent.setup();
    // userEvent.setup() 会给 jsdom 装上自己的 clipboard polyfill —— 必须在此
    // 之后覆写，beforeEach 里的 defineProperty 会被 setup 覆盖。
    Object.defineProperty(navigator, 'clipboard', {
      configurable: true,
      value: { writeText: vi.fn().mockResolvedValue(undefined) },
    });
    // 模拟从分享链接进入（地址栏带 session_id）。
    window.history.replaceState({}, '', '/story?session_id=s1');
    render(<StoryPage />);
    await screen.findByText('该会话暂无内容。');

    await user.click(screen.getByRole('button', { name: '分享' }));
    expect(navigator.clipboard.writeText).toHaveBeenCalledWith(
      expect.stringContaining('/story?session_id=s1')
    );
  });

  it('wires the play button to toggle playback state', async () => {
    fetchMock
      .mockResolvedValueOnce(jsonOk({ messages: [{ id: 'm1', role: 'assistant', content: '第一条' }, { id: 'm2', role: 'assistant', content: '第二条' }] }))
      .mockResolvedValueOnce(jsonOk({ map_state: null }));

    const user = userEvent.setup();
    render(<StoryPage />);
    await screen.findByText('第一条');

    const play = screen.getByRole('button', { name: '播放' });
    await user.click(play);
    // 播放中 → 图标与 aria-label 切换为「暂停」
    expect(await screen.findByRole('button', { name: '暂停' })).toBeInTheDocument();
    await user.click(screen.getByRole('button', { name: '暂停' }));
    expect(screen.getByRole('button', { name: '播放' })).toBeInTheDocument();
  });
});

describe('applyStoryMapState (#552)', () => {
  it('applies base layer, flies to an explicit MapSpec frame and restores layers', async () => {
    const dispatchAction = vi.fn();
    await applyStoryMapState(
      {
        base_layer: 'streets',
        viewport: { center: [1, 2], zoom: 4, bearing: 0, pitch: 0 },
        mapspec: { view: { center: [104, 30], zoom: 9, bearing: 0, pitch: 0, framed: true } },
        layers: [{ id: 'L1', name: 'A', type: 'vector', visible: true, opacity: 1, source: { type: 'FeatureCollection', features: [] } }],
      },
      'sid-1',
      new AbortController().signal,
      dispatchAction,
    );

    expect(useHudStore.getState().baseLayer).toBe('streets');
    expect(dispatchAction).toHaveBeenCalledWith({
      command: 'fly_to',
      params: { center: [104, 30], zoom: 9, bearing: 0, pitch: 0 },
    });
    expect(useHudStore.getState().layers).toHaveLength(1);
  });
});
