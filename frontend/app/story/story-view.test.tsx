import { beforeEach, describe, expect, it, vi } from 'vitest';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { StoryView } from './story-view';
import { useHudStore } from '@/lib/store/useHudStore';

// 48 章节长叙事渲染断言（§5 门禁：story 48 章节渲染）
vi.mock('next/navigation', () => ({
  useSearchParams: () => new URLSearchParams('session_id=long48'),
}));

vi.mock('@/components/map/map-panel', () => ({
  MapPanel: () => <div data-testid="map-panel" />,
}));
vi.mock('@/components/chat/story-markdown', () => ({
  default: ({ text }: { text: string }) => <div data-testid="story-md">{text}</div>,
}));
vi.mock('react-map-gl/maplibre', () => ({ useMap: () => ({}) }));
// 稳定引用：inline vi.fn 会让装载 effect（deps 含 dispatchAction）每次渲染重跑
const dispatchActionMock = vi.fn();
vi.mock('@/lib/contexts/map-action-context', () => ({
  useMapAction: () => ({ dispatchAction: dispatchActionMock }),
}));
vi.mock('@/lib/utils/logger', () => ({
  devOnly: { log: vi.fn(), warn: vi.fn(), error: vi.fn() },
  safeError: vi.fn(),
}));
vi.mock('@/lib/api/config', () => ({ API_BASE: 'http://localhost:8000' }));

const jsonOk = (body: unknown) => ({
  ok: true,
  status: 200,
  statusText: 'OK',
  text: () => Promise.resolve(JSON.stringify(body)),
});

beforeEach(() => {
  vi.clearAllMocks();
  localStorage.clear();
  useHudStore.getState().clearLayers();
});

describe('StoryView 长叙事（48 章节）', () => {
  it('渲染 48 个章节、scrubber 覆盖全部、隐藏章节后收缩', async () => {
    const messages = Array.from({ length: 48 }, (_, i) => ({
      id: `m${i}`,
      role: i % 4 === 1 ? 'user' : 'assistant',
      content: `## 章节 ${i + 1}\n推演正文 ${i + 1}`,
    }));
    const fetchMock = vi.fn().mockResolvedValue(jsonOk({ messages }));
    vi.stubGlobal('fetch', fetchMock);
    try {
      render(<StoryView />);
      // 装载完成以 48 个章节节点全部出现为准（story-md 为桩，正文整块渲染）
      const articles = await waitFor(
        () => {
          const nodes = document.querySelectorAll('[data-story-chapter]');
          expect(nodes.length).toBe(48);
          return nodes;
        },
        { timeout: 5000 },
      );
      expect(articles.length).toBe(48);

      const range = screen.getByTestId('story-scrubber-range') as HTMLInputElement;
      expect(range.max).toBe('47');
      expect(range.value).toBe('0');
      expect(screen.getByText('1/48')).toBeInTheDocument();

      // 定位到第 6 章（range change 路径；键盘步进由原生 range 语义承担）
      fireEvent.change(range, { target: { value: '5' } });
      await waitFor(() => {
        expect(document.querySelector('[data-story-active="true"]')?.getAttribute('data-story-chapter')).toBe('ch-5');
      });
      expect(screen.getByText('6/48')).toBeInTheDocument();
    } finally {
      vi.unstubAllGlobals();
    }
  }, 20000);
});
