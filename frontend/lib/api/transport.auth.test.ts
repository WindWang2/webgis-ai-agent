import { describe, it, expect, vi, beforeAll, beforeEach, afterEach } from 'vitest';
import { apiFetch, apiFetchBlob, ApiError } from './transport';
import { setAuth, clearAuth, getAccessToken } from '../auth/tokenStore';
import { installInMemoryLocalStorage } from '../../test/in-memory-local-storage';

/**
 * Bearer wiring for the round-2 auth hardening: data-fabric writes and
 * /chat/tools now require authentication, so the transport must
 *   - attach Authorization when signed in,
 *   - recover ONCE from a 401 via the refresh token,
 *   - skip auth recovery on auth endpoints themselves (skipAuth).
 */

const mockFetch = vi.fn();
vi.stubGlobal('fetch', mockFetch);

const jsonOk = (body: unknown, status = 200) => ({
  ok: true,
  status,
  statusText: 'OK',
  headers: { get: () => null },
  text: () => Promise.resolve(JSON.stringify(body)),
  json: () => Promise.resolve(body),
});

const errResponse = (status: number, body?: unknown) => ({
  ok: false,
  status,
  statusText: '',
  headers: { get: () => null },
  text: () =>
    Promise.resolve(body === undefined ? '' : JSON.stringify(body)),
  json: () => Promise.resolve(body),
});

beforeAll(() => {
  installInMemoryLocalStorage();
});

beforeEach(() => {
  vi.clearAllMocks();
  window.localStorage.clear();
  clearAuth();
});

afterEach(() => {
  clearAuth();
});

describe('transport auth (Bearer attach + 401 recovery)', () => {
  it('attaches Authorization when signed in', async () => {
    setAuth({ accessToken: 'acc-1', refreshToken: null }, null);
    mockFetch.mockResolvedValueOnce(jsonOk({ ok: true }));

    await apiFetch('/data-fabric/sources', {
      method: 'POST',
      body: { name: 'x' },
    });

    const [, init] = mockFetch.mock.calls[0];
    expect(init.headers['Authorization']).toBe('Bearer acc-1');
  });

  it('attaches no Authorization header when signed out', async () => {
    mockFetch.mockResolvedValueOnce(jsonOk({ sources: [] }));
    await apiFetch('/data-fabric/sources');
    const [, init] = mockFetch.mock.calls[0];
    expect(init.headers['Authorization']).toBeUndefined();
  });

  it('honors skipAuth (no header even when signed in)', async () => {
    setAuth({ accessToken: 'acc-1', refreshToken: null }, null);
    mockFetch.mockResolvedValueOnce(jsonOk({ access_token: 'new' }));

    await apiFetch('/auth/login', { method: 'POST', skipAuth: true });

    const [, init] = mockFetch.mock.calls[0];
    expect(init.headers['Authorization']).toBeUndefined();
  });

  it('refreshes once after a 401 and retries the original request', async () => {
    setAuth({ accessToken: 'stale', refreshToken: 'ref-1' }, { id: 'u1', username: 'ops' });
    // Original attempt → 401; refresh → new pair; retry with fresh token → ok.
    mockFetch
      .mockResolvedValueOnce(errResponse(401, { detail: 'Not authenticated' }))
      .mockResolvedValueOnce(
        jsonOk({ access_token: 'fresh', refresh_token: 'ref-2', user: { id: 'u1', username: 'ops' } })
      )
      .mockResolvedValueOnce(jsonOk({ success: true }));

    const out = await apiFetch('/data-fabric/sources/e2e/probe', { method: 'POST' });

    expect(out).toEqual({ success: true });
    expect(mockFetch).toHaveBeenCalledTimes(3);
    const [retryUrl, retryInit] = mockFetch.mock.calls[2];
    expect(String(retryUrl)).toContain('/probe');
    expect(retryInit.headers['Authorization']).toBe('Bearer fresh');
    expect(getAccessToken()).toBe('fresh');
  });

  it('retries a POST after refresh (a 401 was never processed server-side)', async () => {
    setAuth({ accessToken: 'stale', refreshToken: 'ref-1' }, null);
    mockFetch
      .mockResolvedValueOnce(errResponse(401))
      .mockResolvedValueOnce(jsonOk({ access_token: 'fresh' }))
      .mockResolvedValueOnce(jsonOk({ created: true }));

    await apiFetch('/data-fabric/sources', {
      method: 'POST',
      body: { name: 'src' },
    });
    expect(mockFetch).toHaveBeenCalledTimes(3);
  });

  it('does not refresh when the request already used the fresh token (single retry)', async () => {
    setAuth({ accessToken: 'stale', refreshToken: 'ref-1' }, null);
    mockFetch
      .mockResolvedValueOnce(errResponse(401))
      .mockResolvedValueOnce(jsonOk({ access_token: 'fresh' }))
      .mockResolvedValueOnce(errResponse(401)); // still rejected

    const err = (await apiFetch('/x').catch((e: unknown) => e)) as ApiError;
    expect(err).toBeInstanceOf(ApiError);
    expect(err.status).toBe(401);
    expect(mockFetch).toHaveBeenCalledTimes(3); // attempt + refresh + one retry
  });

  it('does not attempt refresh with no refresh token', async () => {
    setAuth({ accessToken: 'acc-only', refreshToken: null }, null);
    mockFetch.mockResolvedValueOnce(errResponse(401));

    const err = (await apiFetch('/x').catch((e: unknown) => e)) as ApiError;
    expect(err.status).toBe(401);
    expect(mockFetch).toHaveBeenCalledTimes(1);
  });

  it('surfaces the 401 (no loop) when the refresh itself is rejected', async () => {
    setAuth({ accessToken: 'stale', refreshToken: 'revoked' }, null);
    mockFetch
      .mockResolvedValueOnce(errResponse(401))
      .mockResolvedValueOnce(errResponse(401, { detail: 'Token revoked' }));

    const err = (await apiFetch('/x').catch((e: unknown) => e)) as ApiError;
    expect(err.status).toBe(401);
    expect(mockFetch).toHaveBeenCalledTimes(2);
    // Definitive rejection drops local credentials.
    expect(getAccessToken()).toBeNull();
  });

  it('does not run auth recovery on a non-401 error', async () => {
    setAuth({ accessToken: 'stale', refreshToken: 'ref-1' }, null);
    mockFetch.mockResolvedValueOnce(errResponse(500));

    const err = (await apiFetch('/x').catch((e: unknown) => e)) as ApiError;
    expect(err.status).toBe(500);
    expect(mockFetch).toHaveBeenCalledTimes(1);
  });
});

describe('transport apiFetchBlob (#515: authenticated downloads)', () => {
  const blobOk = (body = 'file-bytes', disposition: string | null = null) => ({
    ok: true,
    status: 200,
    statusText: 'OK',
    headers: { get: (name: string) => (name === 'content-disposition' ? disposition : null) },
    blob: () => Promise.resolve(new Blob([body])),
  });

  it('fetches the blob with the Bearer header attached', async () => {
    setAuth({ accessToken: 'acc-dl', refreshToken: null }, null);
    mockFetch.mockResolvedValueOnce(blobOk());

    const out = await apiFetchBlob('/api/v1/export/download/x.png');
    const [, init] = mockFetch.mock.calls[0];
    expect(init.headers.Authorization).toBe('Bearer acc-dl');
    expect(out.filename).toBeNull();
    expect(out.blob).toBeInstanceOf(Blob);
  });

  it('parses the Content-Disposition filename when present', async () => {
    setAuth({ accessToken: 'acc-dl', refreshToken: null }, null);
    mockFetch.mockResolvedValueOnce(blobOk('x', 'attachment; filename="map_export_1.png"'));

    const out = await apiFetchBlob('/api/v1/export/download/map_export_1.png');
    expect(out.filename).toBe('map_export_1.png');
  });

  it('surfaces a non-ok status as ApiError (401 → caller shows login prompt)', async () => {
    setAuth({ accessToken: 'stale-dl', refreshToken: null }, null);
    mockFetch.mockResolvedValueOnce(errResponse(401));

    const err = (await apiFetchBlob('/api/v1/export/download/x.png').catch((e: unknown) => e)) as ApiError;
    expect(err).toBeInstanceOf(ApiError);
    expect(err.status).toBe(401);
  });

  it('recovers ONCE from a 401 via the refresh token like apiFetch', async () => {
    setAuth({ accessToken: 'stale-dl', refreshToken: 'ref-dl' }, null);
    mockFetch
      .mockResolvedValueOnce(errResponse(401))
      .mockResolvedValueOnce(jsonOk({ access_token: 'fresh-dl', refresh_token: 'ref-2' }))
      .mockResolvedValueOnce(blobOk('bytes'));

    const out = await apiFetchBlob('/api/v1/export/download/x.png');
    expect(out.blob).toBeInstanceOf(Blob);
    expect(mockFetch).toHaveBeenCalledTimes(3);
  });
});
