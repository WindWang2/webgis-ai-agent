import { beforeEach, describe, expect, it, vi } from 'vitest';
import { act, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { SearchDrawer } from './search-drawer';
import { useSearchDrawerStore } from '@/lib/hooks/use-search-drawer';
import { upsertIndexedSession, clearSearchIndex } from '@/lib/search/session-index';
import { setPendingLocate } from '@/lib/search/locate';
import { consumePendingLocate } from '@/lib/search/locate';
import { useHudStore } from '@/lib/store/useHudStore';

vi.mock('@/lib/api/transport', async (importOriginal) => {
  const orig = await importOriginal<typeof import('@/lib/api/transport')>();
  return {
    ...orig,
    apiFetch: vi.fn().mockRejectedValue(new Error('offline')),
  };
});

const storage = new Map<string, string>();
beforeEach(() => {
  storage.clear();
  vi.mocked(localStorage.getItem).mockImplementation((k: string) => storage.get(k) ?? null);
  vi.mocked(localStorage.setItem).mockImplementation((k: string, v: string) => {
    storage.set(k, v);
  });
  vi.mocked(localStorage.removeItem).mockImplementation((k: string) => {
    storage.delete(k);
  });
  clearSearchIndex();
  useSearchDrawerStore.getState().closeDrawer();
  useHudStore.setState({ layers: [] });
  // 预置索引：1 会话（标题+消息+产物 ref）
  upsertIndexedSession({
    id: 'sess-1',
    title: '长江热力图会话',
    updatedAt: 100,
    docs: [
      { messageIndex: 0, role: 'user', text: '看看热力图', refs: [] },
      { messageIndex: 1, role: 'assistant', text: '热力图已生成，产物 ref:chart-h1', refs: ['ref:chart-h1'] },
    ],
  });
});

describe('SearchDrawer', () => {
  it('会话命中点击 → onSelectSession', async () => {
    const onSelect = vi.fn();
    render(<SearchDrawer onSelectSession={onSelect} />);
    act(() => useSearchDrawerStore.getState().openDrawer());
    const input = await screen.findByTestId('search-input');
    fireEvent.change(input, { target: { value: '长江' } });
    const hits = await screen.findAllByTestId('search-hit');
    fireEvent.click(hits[0]);
    expect(onSelect).toHaveBeenCalledWith('sess-1');
    expect(useSearchDrawerStore.getState().isOpen).toBe(false);
  });

  it('消息命中点击 → 恢复会话 + pendingLocate 通道置位', async () => {
    const onSelect = vi.fn();
    render(<SearchDrawer onSelectSession={onSelect} />);
    act(() => useSearchDrawerStore.getState().openDrawer());
    const input = await screen.findByTestId('search-input');
    fireEvent.change(input, { target: { value: '热力图' } });
    await waitFor(() => expect(screen.getAllByTestId('search-group-message')).toHaveLength(1));
    await waitFor(() => expect(screen.getAllByTestId('search-group-artifact')).toHaveLength(1));
    const messageHits = screen.getAllByTestId('search-group-message');
    fireEvent.click(messageHits[0].querySelector('[data-testid="search-hit"]')!);
    expect(onSelect).toHaveBeenCalledWith('sess-1');
    expect(consumePendingLocate('sess-1')).toBe(0);
  });

  it('图层命中 → 切到图层面板，不出会话', async () => {
    useHudStore.setState({
      layers: [{ id: 'L1', name: 'DEM 渲染', type: 'raster', visible: true, opacity: 1 }],
    } as never);
    const onSelect = vi.fn();
    render(<SearchDrawer onSelectSession={onSelect} />);
    act(() => useSearchDrawerStore.getState().openDrawer());
    const input = await screen.findByTestId('search-input');
    fireEvent.change(input, { target: { value: 'DEM' } });
    const hits = await screen.findAllByTestId('search-hit');
    fireEvent.click(hits[0]);
    expect(useHudStore.getState().activeLeftTab).toBe('layers');
    expect(onSelect).not.toHaveBeenCalled();
  });

  it('索引状态条展示本地索引边界（LRU 上限诚实披露）', async () => {
    render(<SearchDrawer onSelectSession={vi.fn()} />);
    act(() => useSearchDrawerStore.getState().openDrawer());
    await waitFor(() => expect(screen.getByTestId('search-status')).toHaveTextContent('本地索引'));
    expect(screen.getByTestId('search-status')).toHaveTextContent('LRU');
  });

  it('消息命中 → chat 侧消费：consumePendingLocate 与 setPendingLocate 配对', () => {
    setPendingLocate('sx', 9);
    expect(consumePendingLocate('sx')).toBe(9);
  });
});
