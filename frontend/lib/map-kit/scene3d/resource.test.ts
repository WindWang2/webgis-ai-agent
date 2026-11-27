/**
 * scene3d/resource 预算读取契约：HTTP 错误 / content-length 超限时不得
 * 悬挂响应体（body 必须 cancel，批量 404 不累积连接）。
 */
import { afterEach, describe, expect, it, vi } from 'vitest';

import { fetchJson } from './resource';

function responseLike(over: Record<string, unknown>): Response {
  return {
    ok: true,
    status: 200,
    headers: new Headers(),
    body: null,
    ...over,
  } as unknown as Response;
}

afterEach(() => {
  vi.unstubAllGlobals();
});

describe('scene3d resource body budget', () => {
  it('cancels the response body on http_error', async () => {
    const cancel = vi.fn().mockResolvedValue(undefined);
    vi.stubGlobal('fetch', vi.fn(async () => responseLike({
      ok: false,
      status: 404,
      body: { cancel },
    })));

    await expect(fetchJson('https://assets.test/missing.json')).rejects.toMatchObject({
      code: 'http_error',
    });
    expect(cancel).toHaveBeenCalledTimes(1);
  });

  it('cancels the response body when declared content-length exceeds the budget', async () => {
    const cancel = vi.fn().mockResolvedValue(undefined);
    vi.stubGlobal('fetch', vi.fn(async () => responseLike({
      headers: new Headers({ 'content-length': '9999' }),
      body: { cancel },
    })));

    await expect(
      fetchJson('https://assets.test/huge.json', { maxBytes: 10 }),
    ).rejects.toMatchObject({ code: 'payload_too_large' });
    expect(cancel).toHaveBeenCalledTimes(1);
  });
});
