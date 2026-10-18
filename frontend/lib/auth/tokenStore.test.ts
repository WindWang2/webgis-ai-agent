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
    const stored = JSON.parse(window.localStorage.getItem(KEY) ?? '{}');
    expect(stored).toMatchObject({
      accessToken: 'acc-1',
    });
    // FRONT-03: refreshToken must NOT be stored in plaintext localStorage
    expect(stored.refreshToken).toBeUndefined();
  });

  it('purges legacy plaintext refreshToken if present in localStorage (FRONT-03)', () => {
    window.localStorage.setItem(
      KEY,
      JSON.stringify({
        accessToken: 'legacy-acc',
        refreshToken: 'legacy-refresh-leaked',
        user: { id: 'u1', username: 'legacy' },
      }),
    );
    window.dispatchEvent(new StorageEvent('storage', { key: KEY }));
    expect(getAccessToken()).toBe('legacy-acc');
    const stored = JSON.parse(window.localStorage.getItem(KEY) ?? '{}');
    expect(stored.refreshToken).toBeUndefined();
    expect(getRefreshToken()).toBeNull();
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
    expect(new URL(String(url), 'http://x').pathname).toBe('/api/v1/auth/refresh');
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

// ─── F-06 / F-14: zombie sessions and cross-tab identity mixing ─────────

function jwt(payload: Record<string, unknown>): string {
  const b64 = (o: unknown) =>
    btoa(JSON.stringify(o)).replace(/=+$/, '').replace(/\+/g, '-').replace(/\//g, '_');
  return `${b64({ alg: 'HS256', typ: 'JWT' })}.${b64(payload)}.sig`;
}

describe('F-06: memory-only refresh token cannot leave a zombie session', () => {
  afterEach(() => vi.resetModules());

  it('after a reload, an expired persisted access token is dropped (signed out)', async () => {
    const expired = jwt({ sub: 'u1', exp: Math.floor(Date.now() / 1000) - 60 });
    window.localStorage.setItem(KEY, JSON.stringify({ accessToken: expired, user: { id: 'u1', username: 'ops' } }));
    vi.resetModules(); // simulate a page reload: fresh module, no in-memory refresh token
    const store = await import('./tokenStore');
    expect(store.getAccessToken()).toBeNull();
    expect(store.getAuthUser()).toBeNull();
    expect(window.localStorage.getItem(KEY)).toBeNull();
  });

  it('after a reload, a still-valid access token is kept', async () => {
    const valid = jwt({ sub: 'u1', exp: Math.floor(Date.now() / 1000) + 600 });
    window.localStorage.setItem(KEY, JSON.stringify({ accessToken: valid, user: { id: 'u1', username: 'ops' } }));
    vi.resetModules();
    const store = await import('./tokenStore');
    expect(store.getAccessToken()).toBe(valid);
  });

  it('isAccessTokenExpired only trusts decodable JWT exp', async () => {
    const { isAccessTokenExpired } = await import('./tokenStore');
    expect(isAccessTokenExpired('opaque-token')).toBe(false);
    expect(isAccessTokenExpired(null)).toBe(false);
    expect(isAccessTokenExpired(jwt({ exp: 1 }))).toBe(true);
    expect(isAccessTokenExpired(jwt({ exp: Math.floor(Date.now() / 1000) + 600 }))).toBe(false);
  });
});

describe('F-14: cross-tab storage sync never mixes identities', () => {
  function storageEvent(newValue: string | null): void {
    window.dispatchEvent(new StorageEvent('storage', { key: KEY, newValue }));
  }

  it('another tab signing into a different account drops this tab\'s refresh token', () => {
    setAuth({ accessToken: 'acc-Y', refreshToken: 'ref-Y' }, { id: 'Y', username: 'y' });
    const other = JSON.stringify({ accessToken: 'acc-X', user: { id: 'X', username: 'x' } });
    window.localStorage.setItem(KEY, other);
    storageEvent(other);
    expect(getAccessToken()).toBe('acc-X');
    expect(getAuthUser()?.id).toBe('X');
    expect(getRefreshToken()).toBeNull();
  });

  it('same-account rotation in another tab keeps this tab\'s refresh token', () => {
    setAuth({ accessToken: 'acc-1', refreshToken: 'ref-1' }, { id: 'Y', username: 'y' });
    const other = JSON.stringify({ accessToken: 'acc-2', user: { id: 'Y', username: 'y' } });
    window.localStorage.setItem(KEY, other);
    storageEvent(other);
    expect(getAccessToken()).toBe('acc-2');
    expect(getRefreshToken()).toBe('ref-1');
  });

  it('another tab signing out drops this tab\'s refresh token', () => {
    setAuth({ accessToken: 'acc-1', refreshToken: 'ref-1' }, { id: 'Y', username: 'y' });
    window.localStorage.removeItem(KEY);
    storageEvent(null);
    expect(getAccessToken()).toBeNull();
    expect(getRefreshToken()).toBeNull();
  });
});
