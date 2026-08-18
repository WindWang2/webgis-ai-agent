import { describe, it, expect, vi, beforeAll, beforeEach, afterEach } from 'vitest';
import { installInMemoryLocalStorage } from '../../test/in-memory-local-storage';
import {
  getAccessToken,
  getRefreshToken,
  getAuthUser,
  setAuth,
  clearAuth,
  subscribeAuth,
  refreshAuthToken,
} from './tokenStore';

/**
 * Round-2 audit auth wiring: data-fabric write endpoints now require Bearer
 * auth, and the client needs a token store the transport can read. These
 * tests pin the store contract: persistence, notification, and the
 * single-flight refresh used by the transport's 401 recovery.
 */

const KEY = 'webgis_auth';

beforeAll(() => {
  installInMemoryLocalStorage();
});

beforeEach(() => {
  window.localStorage.clear();
  clearAuth();
});

afterEach(() => {
  vi.unstubAllGlobals();
});

describe('tokenStore', () => {
  it('starts signed out (no token, no user)', () => {
    expect(getAccessToken()).toBeNull();
    expect(getRefreshToken()).toBeNull();
    expect(getAuthUser()).toBeNull();
  });

  it('setAuth persists tokens + user and round-trips through localStorage', () => {
    setAuth(
      { accessToken: 'acc-1', refreshToken: 'ref-1' },
      { id: 'u1', username: 'ops', role: 'admin' },
    );
    expect(getAccessToken()).toBe('acc-1');
    expect(getRefreshToken()).toBe('ref-1');
    expect(getAuthUser()).toEqual({ id: 'u1', username: 'ops', role: 'admin' });
    expect(JSON.parse(window.localStorage.getItem(KEY) ?? '{}')).toMatchObject({
      accessToken: 'acc-1',
      refreshToken: 'ref-1',
    });
  });

  it('clearAuth drops everything, including the persisted entry', () => {
    setAuth({ accessToken: 'acc-1', refreshToken: 'ref-1' }, null);
    clearAuth();
    expect(getAccessToken()).toBeNull();
    expect(window.localStorage.getItem(KEY)).toBeNull();
  });

  it('notifies subscribers on set and clear, and unsubscribes correctly', () => {
    const fn = vi.fn();
    const unsubscribe = subscribeAuth(fn);
    setAuth({ accessToken: 'a', refreshToken: null }, null);
    expect(fn).toHaveBeenCalledTimes(1);
    clearAuth();
    expect(fn).toHaveBeenCalledTimes(2);
    unsubscribe();
    setAuth({ accessToken: 'b', refreshToken: null }, null);
    expect(fn).toHaveBeenCalledTimes(2);
  });

  it('treats corrupt localStorage content as signed out', () => {
    window.localStorage.setItem(KEY, '{not json');
    expect(getAccessToken()).toBeNull();
    expect(getRefreshToken()).toBeNull();
  });
});

describe('refreshAuthToken', () => {
  const refreshOk = (access: string, refresh = 'ref-2') =>
    new Response(JSON.stringify({ access_token: access, refresh_token: refresh }), {
      status: 200,
    });

  it('resolves false with no refresh token and never fetches', async () => {
    const fetchMock = vi.fn();
    vi.stubGlobal('fetch', fetchMock);
    await expect(refreshAuthToken()).resolves.toBe(false);
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it('exchanges the refresh token for a new pair and persists it', async () => {
    setAuth({ accessToken: 'stale', refreshToken: 'ref-1' }, { id: 'u1', username: 'ops' });
    const fetchMock = vi.fn().mockResolvedValue(refreshOk('fresh'));
    vi.stubGlobal('fetch', fetchMock);

    await expect(refreshAuthToken()).resolves.toBe(true);
    expect(fetchMock).toHaveBeenCalledTimes(1);
    const [url, init] = fetchMock.mock.calls[0];
    expect(String(url)).toContain('/auth/refresh');
    expect(JSON.parse(String(init.body))).toEqual({ refresh_token: 'ref-1' });
    expect(getAccessToken()).toBe('fresh');
    expect(getRefreshToken()).toBe('ref-2');
    // User profile survives a refresh that does not echo one.
    expect(getAuthUser()).toEqual({ id: 'u1', username: 'ops' });
  });

  it('drops local auth when the refresh token is definitively rejected (401)', async () => {
    setAuth({ accessToken: 'stale', refreshToken: 'ref-1' }, null);
    vi.stubGlobal(
      'fetch',
      vi.fn().mockResolvedValue(new Response('{"detail":"revoked"}', { status: 401 })),
    );
    await expect(refreshAuthToken()).resolves.toBe(false);
    expect(getAccessToken()).toBeNull();
    expect(getRefreshToken()).toBeNull();
  });

  it('keeps tokens on transient refresh failures (429 / 5xx)', async () => {
    setAuth({ accessToken: 'stale', refreshToken: 'ref-1' }, null);
    for (const status of [429, 500, 503]) {
      vi.stubGlobal(
        'fetch',
        vi.fn().mockResolvedValue(new Response('{"detail":"slow down"}', { status })),
      );
      await expect(refreshAuthToken()).resolves.toBe(false);
      expect(getAccessToken()).toBe('stale');
      expect(getRefreshToken()).toBe('ref-1');
    }
  });

  it('does not clobber credentials changed while a refresh was in flight', async () => {
    setAuth({ accessToken: 'stale', refreshToken: 'ref-1' }, null);
    let resolveFetch!: (r: Response) => void;
    const fetchMock = vi
      .fn()
      .mockImplementation(() => new Promise<Response>((res) => (resolveFetch = res)));
    vi.stubGlobal('fetch', fetchMock);

    const p = refreshAuthToken();
    // User switches account mid-flight (login as someone else).
    await vi.waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(1));
    setAuth({ accessToken: 'account-b', refreshToken: 'ref-b' }, { id: 'b', username: 'b' });
    resolveFetch(
      new Response(JSON.stringify({ access_token: 'account-a-fresh', refresh_token: 'ref-a-2' }), {
        status: 200,
      }),
    );
    await expect(p).resolves.toBe(false);
    // Account B's state survives; A's refreshed pair was discarded.
    expect(getAccessToken()).toBe('account-b');
    expect(getRefreshToken()).toBe('ref-b');
  });

  it('keeps tokens on a network failure (transient) and resolves false', async () => {
    setAuth({ accessToken: 'stale', refreshToken: 'ref-1' }, null);
    vi.stubGlobal('fetch', vi.fn().mockRejectedValue(new TypeError('offline')));
    await expect(refreshAuthToken()).resolves.toBe(false);
    expect(getAccessToken()).toBe('stale');
    expect(getRefreshToken()).toBe('ref-1');
  });

  it('shares one in-flight refresh across concurrent callers', async () => {
    setAuth({ accessToken: 'stale', refreshToken: 'ref-1' }, null);
    let resolveFetch!: (r: Response) => void;
    const fetchMock = vi
      .fn()
      .mockImplementation(() => new Promise<Response>((res) => (resolveFetch = res)));
    vi.stubGlobal('fetch', fetchMock);

    const p1 = refreshAuthToken();
    const p2 = refreshAuthToken();
    // The refresh body awaits a dynamic import before fetch — let it start.
    await vi.waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(1));
    resolveFetch(refreshOk('fresh'));
    await expect(p1).resolves.toBe(true);
    await expect(p2).resolves.toBe(true);
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });
});

// ─── 测试阶段免登录（NEXT_PUBLIC_AUTH_DISABLED）────────────────────────

describe('auth bypass (NEXT_PUBLIC_AUTH_DISABLED=true)', () => {
  afterEach(() => {
    delete process.env.NEXT_PUBLIC_AUTH_DISABLED;
    vi.resetModules();
  });

  it('returns the synthetic test-admin when not signed in', async () => {
    process.env.NEXT_PUBLIC_AUTH_DISABLED = 'true';
    vi.resetModules();
    const store = await import('./tokenStore');
    const user = store.getAuthUser();
    expect(user?.id).toBe('test-admin');
    expect(user?.role).toBe('admin');
  });

  it('a real login takes precedence over the bypass identity', async () => {
    process.env.NEXT_PUBLIC_AUTH_DISABLED = 'true';
    vi.resetModules();
    const store = await import('./tokenStore');
    store.setAuth(
      { accessToken: 'at', refreshToken: 'rt' },
      { id: 'u1', username: 'real-user', role: 'viewer' },
    );
    expect(store.getAuthUser()?.id).toBe('u1');
  });

  it('bypass off (default) keeps anonymous null', async () => {
    delete process.env.NEXT_PUBLIC_AUTH_DISABLED;
    vi.resetModules();
    const store = await import('./tokenStore');
    expect(store.getAuthUser()).toBeNull();
  });
});
