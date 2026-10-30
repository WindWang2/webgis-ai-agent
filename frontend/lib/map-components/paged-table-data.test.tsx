/**
 * usePagedLayerTable（Track B · M4）hook 契约：
 * - 首页/续页/诚实计数；
 * - 查询身份（order）变化 → AbortController 作废 in-flight：旧响应迟到不落屏；
 * - 409 → conflict 错误态 + reload 恢复；
 * - 400（畸形谓词）→ badQuery 不重试。
 */
import { describe, expect, it, beforeEach, vi } from 'vitest';
import { render, screen, waitFor, act, fireEvent } from '@testing-library/react';
import { usePagedLayerTable } from '@/lib/map-components/paged-table-data';
import { PagedRevisionConflict } from '@/lib/data-plane/paged-features';
import { setSessionIdentity } from '@/lib/store/session-identity';
import type { Layer } from '@/lib/types/layer';

vi.mock('@/lib/data-plane/paged-features', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@/lib/data-plane/paged-features')>();
  return {
    ...actual,
    fetchFeaturePage: vi.fn(),
  };
});

import { fetchFeaturePage } from '@/lib/data-plane/paged-features';

const mockFetch = vi.mocked(fetchFeaturePage);

function makeLayer(): Layer {
  return {
    id: 'mvt-layer',
    name: '学校',
    type: 'circle',
    visible: true,
    source: { type: 'vector' },
    _tileUrl: '/api/v1/layers/data/ref:schools/tiles/{z}/{x}/{y}.mvt?session_id=session-hooktest-00001',
    _refId: 'ref:schools',
    _descriptor: { mvt_capable: true, feature_count: 90000, content_revision: 3 },
  } as unknown as Layer;
}

function pageOf(features: Array<Record<string, unknown>>, extra: Record<string, unknown> = {}) {
  return {
    features,
    nextCursor: null,
    hasMore: false,
    revision: 3,
    featureCount: 90000,
    bboxMode: null,
    totalMatching: null,
    isComplete: true,
    truncated: false,
    reasonCodes: [],
    idMode: 'native' as const,
    duplicateIdCount: 0,
    ...extra,
  };
}

/** 最小 harness：暴露 hook 状态 + 触发器。 */
function Harness({ layer, order }: { layer: Layer; order?: string }) {
  const sort = order ? { column: order.replace('-', ''), dir: (order.startsWith('-') ? -1 : 1) as 1 | -1 } : null;
  const table = usePagedLayerTable({ layer, sort, searchText: '', bbox: null });
  return (
    <div>
      <div data-testid="status">{table.status}</div>
      <div data-testid="rows">{table.rows.map((r) => r.rowId).join(',')}</div>
      <div data-testid="error">{table.error ? `${table.error.kind}:${table.error.currentRevision ?? ''}` : ''}</div>
      <div data-testid="matched">{table.totalMatching ?? '?'}</div>
      <button type="button" data-testid="more" onClick={table.loadMore}>more</button>
      <button type="button" data-testid="reload" onClick={table.reload}>reload</button>
    </div>
  );
}

describe('usePagedLayerTable', () => {
  beforeEach(() => {
    mockFetch.mockReset();
    act(() => {
      setSessionIdentity('session-hooktest-00001', null);
    });
  });

  it('first page then load-more appends via cursor', async () => {
    const layer = makeLayer();
    mockFetch.mockResolvedValueOnce(pageOf(
      [{ id: 'a', properties: { n: 1 } }, { id: 'b', properties: { n: 2 } }],
      { nextCursor: 'c1', hasMore: true, isComplete: false, totalMatching: 5 },
    ));
    mockFetch.mockResolvedValueOnce(pageOf(
      [{ id: 'c', properties: { n: 3 } }],
      { totalMatching: 5 },
    ));
    render(<Harness layer={layer} />);
    await waitFor(() => {
      expect(screen.getByTestId('rows').textContent).toBe('a,b');
    });
    expect(screen.getByTestId('matched').textContent).toBe('5');
    fireEvent.click(screen.getByTestId('more'));
    await waitFor(() => {
      expect(screen.getByTestId('rows').textContent).toBe('a,b,c');
    });
    expect(mockFetch.mock.calls[1][0]).toMatchObject({ cursor: 'c1', revision: 3 });
  });

  it('sort change aborts in-flight first page; stale response never lands', async () => {
    let resolvePending: (v: unknown) => void = () => {};
    // 显式响应队列（先注册再 render —— mock 装配顺序即调用顺序）。
    const responders: Array<() => Promise<unknown>> = [
      () => new Promise((res) => { resolvePending = res; }),
    ];
    mockFetch.mockImplementation(() => {
      const next = responders.shift();
      return next ? next() : Promise.resolve(pageOf([]));
    });
    const { rerender } = render(<Harness layer={makeLayer()} />);
    // 等首请求发出（loading 中）
    await waitFor(() => {
      expect(mockFetch).toHaveBeenCalledTimes(1);
    });
    // 换排序 → 新查询身份
    responders.push(() => Promise.resolve(pageOf([{ id: 'sorted', properties: { n: 1 } }])));
    rerender(<Harness layer={makeLayer()} order="-students" />);
    await waitFor(() => {
      expect(mockFetch).toHaveBeenCalledTimes(2);
      expect(mockFetch.mock.calls[1][0].order).toBe('-students');
      expect(screen.getByTestId('rows').textContent).toBe('sorted');
    });
    // 旧响应迟到 —— 不得落屏（controller 已 abort，回调直接丢弃）
    resolvePending(pageOf([{ id: 'stale', properties: { n: 0 } }]));
    await act(async () => { await new Promise((r) => setTimeout(r, 20)); });
    expect(screen.getByTestId('rows').textContent).toBe('sorted');
    expect(screen.getByTestId('status').textContent).toBe('ready');
  });

  it('409 conflict → error state with current revision; reload refetches', async () => {
    mockFetch.mockRejectedValueOnce(new PagedRevisionConflict(9));
    render(<Harness layer={makeLayer()} />);
    await waitFor(() => {
      expect(screen.getByTestId('error').textContent).toBe('conflict:9');
    });
    mockFetch.mockResolvedValueOnce(pageOf([{ id: 'fresh', properties: { n: 1 } }], { revision: 9 }));
    fireEvent.click(screen.getByTestId('reload'));
    await waitFor(() => {
      expect(screen.getByTestId('rows').textContent).toBe('fresh');
      expect(screen.getByTestId('error').textContent).toBe('');
    });
  });

  it('badQuery (400) fails fast without retries', async () => {
    mockFetch.mockRejectedValueOnce(Object.assign(new Error('bad'), { status: 400 }));
    render(<Harness layer={makeLayer()} />);
    await waitFor(() => {
      expect(screen.getByTestId('error').textContent).toContain('badQuery');
    });
    expect(mockFetch).toHaveBeenCalledTimes(1);
  });

  it('positions row ids from projected positional ids (idMode=positional)', async () => {
    mockFetch.mockResolvedValueOnce(pageOf(
      [{ id: 4, properties: { name: 'x' } }, { id: 7, properties: { name: 'y' } }],
      { idMode: 'positional' },
    ));
    render(<Harness layer={makeLayer()} />);
    await waitFor(() => {
      expect(screen.getByTestId('rows').textContent).toBe('4,7');
    });
    expect(mockFetch.mock.calls[0][0]).toMatchObject({
      sessionId: 'session-hooktest-00001',
      refId: 'ref:schools',
      revision: 3,
      limit: 200,
    });
  });
});
