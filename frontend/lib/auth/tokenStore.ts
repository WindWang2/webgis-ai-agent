/**
 * Client-side auth token store (JWT access + refresh pair).
 *
 * The backend has had Bearer-auth endpoints since S41 (/auth/login,
 * /auth/refresh, /auth/logout), and round-2 audit hardening made the
 * data-fabric write paths (create/delete/probe/sync/preview/query/
 * materialize) require authentication. Until now the shipped UI had NO
 * ability to hold or send a Bearer token, which made those endpoints — and
 * the Data Sources tab that drives them — unusable for every real user.
 * This store plus the transport wiring below closes that gap.
 *
 * Storage: localStorage under one JSON key. The access token is short-lived
 * (30 min) and the refresh token is rotated on every refresh; neither is a
 * long-lived password. localStorage (not a cookie) keeps the token out of
 * every cross-site request automatically — the transport attaches it
 * explicitly per request.
 */

const STORAGE_KEY = 'webgis_auth';

// 测试阶段免登录（与后端 AUTH_DISABLED 配对）：开启时未登录也视为
// test-admin(admin)，登录态门控的 UI（#469 导出、#528 项目 tab 等）全部
// 放行。真实登录（localStorage 有凭证）始终优先于合成身份。
const AUTH_BYPASS = process.env.NEXT_PUBLIC_AUTH_DISABLED === 'true';
const AUTH_BYPASS_USER: AuthUser = {
  id: 'test-admin',
  username: 'test-admin',
  email: 'test-admin@local.test',
  full_name: '测试管理员（免登录）',
  role: 'admin',
};

export interface AuthUser {
  id: string;
  username: string;
  email?: string | null;
  full_name?: string | null;
  role?: string;
}

export interface AuthTokens {
  accessToken: string;
  refreshToken: string | null;
}

interface StoredAuth extends AuthTokens {
  user: AuthUser | null;
}

let cached: StoredAuth | null = null;
let loaded = false;

const listeners = new Set<() => void>();

// FE-P3-8: cross-tab sync. Without this, a login in tab B left tab A sending
// anonymous requests, and a refresh in tab A could clobber tab B's rotated
// refresh token.
if (isBrowser()) {
  window.addEventListener('storage', (event) => {
    if (event.key === STORAGE_KEY) {
      loaded = false; // force a re-read on next access
      cached = null;
      listeners.forEach((fn) => {
        try {
          fn();
        } catch {
          /* listener errors must not break auth state */
        }
      });
    }
  });
}

function isBrowser(): boolean {
  return typeof window !== 'undefined' && typeof window.localStorage !== 'undefined';
}

function load(): StoredAuth | null {
  if (!isBrowser()) return null;
  if (loaded) return cached;
  loaded = true;
  try {
    const raw = window.localStorage.getItem(STORAGE_KEY);
    if (!raw) return null;
    const parsed = JSON.parse(raw) as StoredAuth;
    if (typeof parsed?.accessToken === 'string' && parsed.accessToken) {
      cached = {
        accessToken: parsed.accessToken,
        refreshToken: typeof parsed.refreshToken === 'string' ? parsed.refreshToken : null,
        user: parsed.user ?? null,
      };
    }
  } catch {
    // Corrupt entry — treat as signed out.
    try {
      window.localStorage.removeItem(STORAGE_KEY);
    } catch {
      /* storage unavailable (private mode quota) — ignore */
    }
  }
  return cached;
}

function persist(next: StoredAuth | null): void {
  cached = next;
  loaded = true;
  if (isBrowser()) {
    try {
      if (next) window.localStorage.setItem(STORAGE_KEY, JSON.stringify(next));
      else window.localStorage.removeItem(STORAGE_KEY);
    } catch {
      /* storage write failed — in-memory state still updated */
    }
  }
  listeners.forEach((fn) => {
    try {
      fn();
    } catch {
      /* a broken listener must not break auth state changes */
    }
  });
}

/** Current access token, or null when signed out. */
export function getAccessToken(): string | null {
  return load()?.accessToken ?? null;
}

/** Current refresh token (null for anonymous sessions). */
export function getRefreshToken(): string | null {
  return load()?.refreshToken ?? null;
}

/** The signed-in user profile from the last login/refresh, if any. */
export function getAuthUser(): AuthUser | null {
  const stored = load()?.user ?? null;
  if (stored) return stored;
  return AUTH_BYPASS ? AUTH_BYPASS_USER : null;
}

/** Persist a login/refresh result and notify listeners. */
export function setAuth(tokens: AuthTokens, user: AuthUser | null): void {
  persist({
    accessToken: tokens.accessToken,
    refreshToken: tokens.refreshToken,
    user,
  });
}

/** Drop all credentials (logout, or refresh definitively rejected). */
export function clearAuth(): void {
  persist(null);
}

/** Minimal reactivity for UI: callback fires on every auth state change. */
export function subscribeAuth(fn: () => void): () => void {
  listeners.add(fn);
  return () => listeners.delete(fn);
}

/**
 * Single-flight token refresh shared by every concurrent 401.
 *
 * Uses plain fetch (NOT apiFetch): the transport itself calls this while
 * handling a 401, so routing through the transport would recurse.
 */
let refreshInFlight: Promise<boolean> | null = null;

export function refreshAuthToken(): Promise<boolean> {
  if (refreshInFlight) return refreshInFlight;
  const startedWith = getRefreshToken();
  if (!startedWith) return Promise.resolve(false);
  refreshInFlight = (async () => {
    try {
      // Late import to avoid a config/init cycle at module load.
      const { API_BASE } = await import('../api/config');
      const res = await fetch(`${API_BASE}/auth/refresh`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ refresh_token: startedWith }),
      });
      if (!res.ok) {
        // 401/403 = the token is definitively revoked/expired — drop the
        // session so the UI falls back to anonymous instead of retrying a
        // dead credential forever. 429 (rate limit) and 5xx are transient:
        // keep the tokens and surface the original 401 to the caller.
        if (res.status === 401 || res.status === 403) clearAuth();
        return false;
      }
      const body = (await res.json()) as {
        access_token: string;
        refresh_token?: string | null;
        user?: AuthUser;
      };
      // Epoch guard: if the user signed in/out (or into another account)
      // while this refresh was in flight, do not clobber the newer state.
      if (getRefreshToken() !== startedWith) return false;
      const existingUser = getAuthUser();
      setAuth(
        {
          accessToken: body.access_token,
          refreshToken: body.refresh_token ?? startedWith,
        },
        body.user ?? existingUser,
      );
      return true;
    } catch {
      // Network error during refresh: keep the (possibly still valid) tokens
      // and report failure — the caller surfaces the original 401.
      return false;
    } finally {
      refreshInFlight = null;
    }
  })();
  return refreshInFlight;
}
