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
  return load()?.user ?? null;
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
  const refreshToken = getRefreshToken();
  if (!refreshToken) return Promise.resolve(false);
  refreshInFlight = (async () => {
    try {
      // Late import to avoid a config/init cycle at module load.
      const { API_BASE } = await import('../api/config');
      const res = await fetch(`${API_BASE}/auth/refresh`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ refresh_token: refreshToken }),
      });
      if (!res.ok) {
        // Revoked/expired refresh token — drop the session so the UI falls
        // back to anonymous instead of retrying a dead credential forever.
        clearAuth();
        return false;
      }
      const body = (await res.json()) as {
        access_token: string;
        refresh_token?: string | null;
        user?: AuthUser;
      };
      const existingUser = getAuthUser();
      setAuth(
        {
          accessToken: body.access_token,
          refreshToken: body.refresh_token ?? refreshToken,
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
