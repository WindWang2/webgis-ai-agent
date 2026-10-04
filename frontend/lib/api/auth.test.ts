import { describe, it, expect, vi, beforeAll, beforeEach, afterEach } from 'vitest';
import { login, logout } from './auth';
import { setAuth, clearAuth } from '../auth/tokenStore';
import { installInMemoryLocalStorage } from '../../test/in-memory-local-storage';

/**
 * F-01: the backend mounts the auth router ONLY under /api/v1
 * (app/main.py include_router(auth_routes.router, prefix="/api/v1")), so the
 * UI must call /api/v1/auth/*; bare /auth/* is a 404 in every environment.
 */

const mockFetch = vi.fn();
vi.stubGlobal('fetch', mockFetch);

const jsonOk = (body: unknown) => ({
  ok: true,
  status: 200,
  statusText: 'OK',
  headers: { get: () => null },
  text: () => Promise.resolve(JSON.stringify(body)),
  json: () => Promise.resolve(body),
});

const pathOf = (url: unknown) => new URL(String(url), 'http://x').pathname;

beforeAll(() => installInMemoryLocalStorage());
beforeEach(() => {
  mockFetch.mockReset();
  window.localStorage.clear();
  clearAuth();
});
afterEach(() => clearAuth());

describe('auth API paths (F-01)', () => {
  it('login posts to /api/v1/auth/login', async () => {
    mockFetch.mockResolvedValueOnce(
      jsonOk({ access_token: 'a', token_type: 'bearer', expires_in: 1, user: { id: 'u', username: 'n' } }),
    );
    await login('n', 'p');
    expect(pathOf(mockFetch.mock.calls[0][0])).toBe('/api/v1/auth/login');
  });

  it('logout posts to /api/v1/auth/logout', async () => {
    setAuth({ accessToken: 'acc', refreshToken: null }, null);
    mockFetch.mockResolvedValueOnce(jsonOk({}));
    await logout();
    expect(pathOf(mockFetch.mock.calls[0][0])).toBe('/api/v1/auth/logout');
  });
});
