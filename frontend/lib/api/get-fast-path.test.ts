/**
 * Tests for the GET Fast Path (in-flight dedup + short-lived cache + generation
 * guard). The whole point of the Fast Path is to stop the same request
 * firing more than once per cycle, so these tests focus on the contract.
 */

import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { fastGet, invalidateCache, clearCache, _cacheSize } from './get-fast-path';

const mockFetch = vi.fn();
vi.stubGlobal('fetch', mockFetch);

/** Build a JSON 200 response double. */
const jsonOk = (body: unknown, status = 200) => ({
  ok: true,
  status,
  statusText: 'OK',
  text: () => Promise.resolve(JSON.stringify(body)),
});

beforeEach(() => {
  vi.clearAllMocks();
  clearCache();
});

afterEach(() => {
  clearCache();
});

describe('fastGet (F-FE-FGP)', () => {
  it('issues a single network request for the same path', async () => {
    const body = { items: [1, 2, 3] };
    mockFetch.mockResolvedValue(jsonOk(body));
    const [a, b, c] = await Promise.all([
      fastGet<typeof body>('/projects'),
      fastGet<typeof body>('/projects'),
      fastGet<typeof body>('/projects'),
    ]);
    expect(mockFetch).toHaveBeenCalledTimes(1);
    expect(a.data).toEqual(body);
    expect(b.data).toEqual(body);
    expect(c.data).toEqual(body);
  });

  it('returns cached=true on the second hit within TTL', async () => {
    mockFetch.mockResolvedValue(jsonOk({ ok: true }));
    const first = await fastGet('/x');
    const second = await fastGet('/x');
    expect(mockFetch).toHaveBeenCalledTimes(1);
    expect(first.cached).toBe(false);
    expect(second.cached).toBe(true);
  });

  it('skips the cache on forceRefresh and refetches', async () => {
    mockFetch.mockResolvedValueOnce(jsonOk({ v: 1 }));
    mockFetch.mockResolvedValueOnce(jsonOk({ v: 2 }));
    const a = await fastGet<{ v: number }>('/x');
    const b = await fastGet<{ v: number }>('/x', { forceRefresh: true });
    expect(a.data?.v).toBe(1);
    expect(b.data?.v).toBe(2);
    expect(mockFetch).toHaveBeenCalledTimes(2);
  });

  it('treats different params as different keys', async () => {
    mockFetch.mockResolvedValue(jsonOk({ ok: true }));
    await fastGet('/items', { params: { id: '1' } });
    await fastGet('/items', { params: { id: '2' } });
    expect(mockFetch).toHaveBeenCalledTimes(2);
  });

  it('treats the same params in different order as the same key', async () => {
    mockFetch.mockResolvedValue(jsonOk({ ok: true }));
    await fastGet('/items', { params: { a: '1', b: '2' } });
    await fastGet('/items', { params: { b: '2', a: '1' } });
    expect(mockFetch).toHaveBeenCalledTimes(1);
  });

  it('invalidateCache evicts matching keys only', async () => {
    mockFetch.mockResolvedValue(jsonOk({ ok: true }));
    await fastGet('/projects');
    await fastGet('/projects/abc/datasets');
    await fastGet('/data-fabric/sources');
    expect(_cacheSize()).toBe(3);
    const removed = invalidateCache('/projects');
    expect(removed).toBe(2);
    expect(_cacheSize()).toBe(1);
  });

  it('clearCache wipes the entire cache', async () => {
    mockFetch.mockResolvedValue(jsonOk({ ok: true }));
    await fastGet('/a');
    await fastGet('/b');
    expect(_cacheSize()).toBe(2);
    clearCache();
    expect(_cacheSize()).toBe(0);
  });

  it('propagates caller abort to the in-flight fetch', async () => {
    let capturedSignal: AbortSignal | undefined;
    mockFetch.mockImplementation(
      (_url: string, init?: RequestInit) =>
        new Promise<Response>((_resolve, reject) => {
          capturedSignal = init?.signal ?? undefined;
          init?.signal?.addEventListener('abort', () => {
            reject(new DOMException('aborted', 'AbortError'));
          });
        })
    );
    const controller = new AbortController();
    const promise = fastGet('/slow', { signal: controller.signal });
    // Caller aborts before the response arrives.
    setTimeout(() => controller.abort(), 5);
    await expect(promise).rejects.toBeInstanceOf(DOMException);
    expect(capturedSignal?.aborted).toBe(true);
  });

  it('dedupes overlapping in-flight calls when ttlMs=0', async () => {
    mockFetch.mockImplementation(
      () => new Promise((resolve) => setTimeout(() => resolve(jsonOk({ ok: true })), 5))
    );
    // Fire three calls before any of them resolves: with ttlMs=0 the cache
    // is bypassed on lookup, but the in-flight Promise is still shared.
    const [a, b, c] = await Promise.all([
      fastGet('/x', { ttlMs: 0 }),
      fastGet('/x', { ttlMs: 0 }),
      fastGet('/x', { ttlMs: 0 }),
    ]);
    expect(mockFetch).toHaveBeenCalledTimes(1);
    expect(a.data).toEqual(b.data);
    expect(b.data).toEqual(c.data);
  });

  it('issues a fresh fetch when ttlMs=0 and the previous in-flight has resolved', async () => {
    mockFetch.mockImplementation(
      () => new Promise((resolve) => setTimeout(() => resolve(jsonOk({ ok: true })), 5))
    );
    await fastGet('/x', { ttlMs: 0 });
    await new Promise((r) => setTimeout(r, 10));
    await fastGet('/x', { ttlMs: 0 });
    expect(mockFetch).toHaveBeenCalledTimes(2);
  });
});

describe('fastGet ownerToken passthrough (#1109)', () => {
  it('forwards ownerToken as the X-Session-Token header', async () => {
    mockFetch.mockResolvedValue(jsonOk({ ok: true }));
    await fastGet('/uploads', { params: { session_id: 's1' }, ownerToken: 'tok-1' });
    const call = mockFetch.mock.calls[0];
    const headers = call[1]?.headers as Record<string, string>;
    expect(headers?.['X-Session-Token']).toBe('tok-1');
  });

  it('omits the header when no ownerToken is given', async () => {
    mockFetch.mockResolvedValue(jsonOk({ ok: true }));
    await fastGet('/uploads', { params: { session_id: 's1' } });
    const call = mockFetch.mock.calls[0];
    const headers = call[1]?.headers as Record<string, string>;
    expect(headers?.['X-Session-Token']).toBeUndefined();
  });
});
