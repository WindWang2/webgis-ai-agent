/**
 * useSelectionHandleTable（G24 / P4）域内契约：
 * - 首页 + loadMore 续页（不漏不重）；
 * - 计数如实（count/count_exact 透传，未知不作假）；
 * - 410 stale → status='stale'（UI 引导重建选择，A22）；
 * - handle 变化 → 旧请求作废（in-flight abort）。
 */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { act, renderHook, waitFor } from '@testing-library/react';

const apiFetchMock = vi.hoisted(() => vi.fn());
vi.mock('@/lib/api/transport', () => ({ apiFetch: apiFetchMock }));
vi.mock('@/lib/store/session-identity', () => ({
  getSessionIdentity: () => ({ sessionId: 'sess-seltable-0001', ownerToken: 'tok' }),
}));

import { useSelectionHandleTable } from '@/lib/map-components/selection-table-data';
import type { Layer } from '@/lib/types/layer';

const layer = {
  id: 'ref:sel-1',
  _refId: 'ref:sel-1',
  _tileUrl: 'http://x/tiles?session_id=sess-seltable-0001',
} as unknown as Layer;

function page(features: unknown[], opts: { next?: string | null; count?: number; countExact?: boolean } = {}) {
  return {
    features,
    pagination: { next_cursor: opts.next ?? null, has_more: !!opts.next, truncated: false, is_complete: !opts.next },
    selection: { count: opts.count !== undefined ? opts.count : features.length, count_exact: opts.countExact ?? true },
    revision: 4,
    reason_codes: ['ids_resolved'],
  };
}

beforeEach(() => { apiFetchMock.mockReset(); });
afterEach(() => { vi.restoreAllMocks(); });

describe('useSelectionHandleTable', () => {
  it('首页 + 续页拼接全集；计数如实', async () => {
    apiFetchMock.mockResolvedValueOnce(
      page([{ id: 1, properties: { i: 1 } }, { id: 2, properties: { i: 2 } }], { next: 'c2', count: 5 }));
    const { result } = renderHook(() => useSelectionHandleTable({ layer, handle: 'h1' }));
    await waitFor(() => expect(result.current.status).toBe('ready'));
    expect(result.current.rows).toHaveLength(2);
    expect(result.current.count).toBe(5);
    expect(result.current.countExact).toBe(true);
    expect(apiFetchMock.mock.calls[0][0]).toContain('/selection/h1/features');

    apiFetchMock.mockResolvedValueOnce(
      page([{ id: 3, properties: { i: 3 } }], { next: null, count: 5 }));
    act(() => { result.current.loadMore(); });
    await waitFor(() => expect(result.current.rows).toHaveLength(3));
    expect(result.current.nextCursor).toBeNull();
    const secondUrl = apiFetchMock.mock.calls[1][0] as string;
    expect(secondUrl).toContain('cursor=c2');
  });

  it('410 stale → status=stale（数据刷新语义）', async () => {
    const err = Object.assign(new Error('stale'), {
      status: 410,
      body: { detail: { error: 'selection_stale', current_revision: 9 } },
    });
    apiFetchMock.mockRejectedValueOnce(err);
    const { result } = renderHook(() => useSelectionHandleTable({ layer, handle: 'h-stale' }));
    await waitFor(() => expect(result.current.status).toBe('stale'));
    expect(result.current.rows).toHaveLength(0);
  });

  it('count_exact=false 时 count 透传为 null/未知（不以已返回行数假冒）', async () => {
    apiFetchMock.mockResolvedValueOnce(
      page([{ id: 1, properties: {} }], { next: 'c9', count: null, countExact: false }));
    const { result } = renderHook(() => useSelectionHandleTable({ layer, handle: 'h2' }));
    await waitFor(() => expect(result.current.status).toBe('ready'));
    expect(result.current.count).toBeNull();
    expect(result.current.countExact).toBe(false);
  });
});
