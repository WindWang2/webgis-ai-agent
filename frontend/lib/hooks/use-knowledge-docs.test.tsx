/**
 * 知识库文档列表 hook 测试 — 分页 / 陈旧响应丢弃 / 删除错误传播。
 */
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { renderHook, act, waitFor } from '@testing-library/react';
import { useKnowledgeDocs } from './use-knowledge-docs';

const mockFetch = vi.fn();
vi.stubGlobal('fetch', mockFetch);

const jsonOk = (body: unknown) => ({
  ok: true,
  status: 200,
  statusText: 'OK',
  headers: { get: () => null },
  text: () => Promise.resolve(JSON.stringify(body)),
});

const envelope = <T,>(data: T) => ({ code: 'SUCCESS', success: true, message: 'ok', data });

const doc = (i: number) => ({
  id: `doc_${i}`,
  title: `文档${i}`,
  file_type: 'text',
  chunk_count: i,
  status: 'completed',
  created_at: '2026-09-01T00:00:00Z',
});

beforeEach(() => {
  vi.clearAllMocks();
});

describe('useKnowledgeDocs', () => {
  it('open 时拉取第一页，loadMore 追加下一页', async () => {
    mockFetch.mockResolvedValueOnce(
      jsonOk(envelope({ total: 3, items: [doc(1), doc(2)] })),
    );
    const { result } = renderHook(() => useKnowledgeDocs(true, 2));
    await waitFor(() => expect(result.current.loading).toBe(false));
    expect(result.current.docs).toHaveLength(2);
    expect(result.current.total).toBe(3);

    mockFetch.mockResolvedValueOnce(jsonOk(envelope({ total: 3, items: [doc(3)] })));
    act(() => result.current.loadMore());
    await waitFor(() => expect(result.current.loadingMore).toBe(false));
    expect(result.current.docs).toHaveLength(3);
    expect(result.current.docs[2]?.id).toBe('doc_3');
  });

  it('删除失败时进入 error 态（诚实报错，不静默）', async () => {
    mockFetch.mockResolvedValueOnce(jsonOk(envelope({ total: 1, items: [doc(1)] })));
    const { result } = renderHook(() => useKnowledgeDocs(true, 20));
    await waitFor(() => expect(result.current.docs).toHaveLength(1));

    mockFetch.mockResolvedValueOnce(
      jsonOk({ code: 'ERR', success: false, message: '删除失败或无权访问该文档', data: null }),
    );
    await act(async () => {
      await result.current.remove('doc_1');
    });
    expect(result.current.error).toBe('删除失败或无权访问该文档');
    expect(result.current.removingId).toBeNull();
  });

  it('删除成功后重置回第一页', async () => {
    mockFetch.mockResolvedValueOnce(
      jsonOk(envelope({ total: 2, items: [doc(1), doc(2)] })),
    );
    const { result } = renderHook(() => useKnowledgeDocs(true, 20));
    await waitFor(() => expect(result.current.docs).toHaveLength(2));

    mockFetch.mockResolvedValueOnce(jsonOk(envelope(null)));
    mockFetch.mockResolvedValueOnce(jsonOk(envelope({ total: 1, items: [doc(2)] })));
    await act(async () => {
      await result.current.remove('doc_1');
    });
    await waitFor(() => expect(result.current.docs).toHaveLength(1));
    expect(result.current.docs[0]?.id).toBe('doc_2');
  });

  it('陈旧响应（seq 落后）被丢弃：refresh 后旧请求返回不覆盖新数据', async () => {
    let resolveFirst: (v: unknown) => void = () => {};
    mockFetch.mockImplementationOnce(
      () => new Promise((resolve) => (resolveFirst = resolve)),
    );
    const { result } = renderHook(() => useKnowledgeDocs(true, 20));
    // 第一个请求仍挂起时用户手动刷新 → seq 前进，第二个请求立即返回。
    mockFetch.mockResolvedValueOnce(jsonOk(envelope({ total: 1, items: [doc(2)] })));
    act(() => result.current.refresh());
    await waitFor(() => expect(result.current.docs).toHaveLength(1));
    // 旧 seq 的响应此刻才到达 —— 必须被丢弃。
    await act(async () => {
      resolveFirst(jsonOk(envelope({ total: 99, items: [doc(99)] })));
    });
    expect(result.current.docs).toHaveLength(1);
    expect(result.current.docs[0]?.id).toBe('doc_2');
    expect(result.current.total).toBe(1);
  });
});
