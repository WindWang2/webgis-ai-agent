/**
 * fetchRefViaApi 适配器契约（#2144 H1）：
 * - 调度器注入的 req.etag 必须以 If-None-Match 回传（否则 304/etag304 全成死代码）；
 * - 无 etag 时不发条件头；
 * - 304（ApiError status=304）→ 'not-modified'；
 * - 200 → { fc, etag }，etag 取自响应头。
 */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { fetchRefViaApi } from './ref-service';
import type { RefFetchRequest } from './scheduler';

function req(over: Partial<RefFetchRequest> = {}): RefFetchRequest {
  return { sessionId: 's1', refId: 'ref:a', ...over };
}

const fetchMock = vi.fn();

beforeEach(() => {
  fetchMock.mockReset();
  vi.stubGlobal('fetch', fetchMock);
});

afterEach(() => {
  vi.unstubAllGlobals();
});

describe('fetchRefViaApi（#2144 H1：If-None-Match 条件请求）', () => {
  it('req.etag 存在 → 请求带 If-None-Match；304 → not-modified', async () => {
    fetchMock.mockResolvedValue({
      ok: false,
      status: 304,
      headers: { get: () => 'W/"v1"' },
      text: async () => '',
    });
    const out = await fetchRefViaApi(req({ etag: 'W/"v1"' }), new AbortController().signal);
    expect(out).toBe('not-modified');
    const init = fetchMock.mock.calls[0][1] as RequestInit;
    expect(new Headers(init.headers).get('If-None-Match')).toBe('W/"v1"');
  });

  it('无 etag → 不发 If-None-Match；200 → { fc, etag } 取自响应头', async () => {
    fetchMock.mockResolvedValue(
      new Response(JSON.stringify({ type: 'FeatureCollection', features: [] }), {
        status: 200,
        headers: { 'Content-Type': 'application/json', ETag: 'W/"v2"' },
      }),
    );
    const out = await fetchRefViaApi(req(), new AbortController().signal);
    expect(out).not.toBe('not-modified');
    if (out !== 'not-modified') {
      expect(out.fc.type).toBe('FeatureCollection');
      expect(out.etag).toBe('W/"v2"');
    }
    const init = fetchMock.mock.calls[0][1] as RequestInit;
    expect(new Headers(init.headers).get('If-None-Match')).toBeNull();
  });

  it('非 FeatureCollection 载荷 fail-closed（502 语义，不被 304 分支吞掉）', async () => {
    fetchMock.mockResolvedValue(
      new Response(JSON.stringify({ type: 'SomethingElse' }), {
        status: 200,
        headers: { 'Content-Type': 'application/json' },
      }),
    );
    await expect(fetchRefViaApi(req(), new AbortController().signal)).rejects.toMatchObject({
      status: 502,
    });
  });
});
