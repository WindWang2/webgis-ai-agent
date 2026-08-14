/**
 * Auth API client: /auth/login and /auth/logout.
 *
 * Registration is intentionally absent — the backend disables public
 * registration by default (ALLOW_PUBLIC_REGISTER); accounts are provisioned
 * by operators via `manage.py create_admin`. Refresh runs inside the token
 * store (plain fetch) so the transport can use it on 401 without a cycle.
 */

import { apiFetch } from './transport';
import {
  clearAuth,
  getAccessToken,
  type AuthUser,
} from '../auth/tokenStore';

interface TokenResponse {
  access_token: string;
  refresh_token?: string | null;
  token_type: string;
  expires_in: number;
  user: AuthUser;
}

/**
 * Sign in and persist the token pair. Importers must call setAuth — this
 * returns the raw response so callers decide persistence (kept side-effect
 * free for tests).
 */
export async function login(
  identifier: string,
  password: string,
): Promise<TokenResponse> {
  return apiFetch<TokenResponse>('/auth/login', {
    method: 'POST',
    body: { identifier, password },
    label: '登录失败',
    skipAuth: true,
  });
}

/** Sign out: bump the server-side token version, then drop local state. */
export async function logout(): Promise<void> {
  try {
    if (getAccessToken()) {
      await apiFetch('/auth/logout', {
        method: 'POST',
        parseJson: false,
        label: '登出失败',
      });
    }
  } finally {
    // Local sign-out must succeed even when the server call fails (offline,
    // 401 from an already-revoked token): never keep credentials around on a
    // user-initiated logout.
    clearAuth();
  }
}
