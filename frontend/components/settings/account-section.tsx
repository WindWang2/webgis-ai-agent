'use client';

import React, { useCallback, useEffect, useState, useSyncExternalStore } from 'react';
import { STitle } from '@/components/shared/section-title';
import { login, logout } from '@/lib/api/auth';
import {
  getAuthUser,
  setAuth,
  subscribeAuth,
  type AuthUser,
} from '@/lib/auth/tokenStore';

/**
 * 账户设置：登录 / 登出（JWT）。
 *
 * Round-2 审计把 data-fabric 写路径（创建/删除/探查/同步/预览/查询/物化）
 * 与 /chat/tools 收紧为需要认证 —— 但已发布前端此前完全没有持有或发送
 * Bearer token 的能力，这些端点（及其驱动的"数据源"页签）对所有真实用户
 * 都会 401。此面板补上客户端认证：
 * - 登录后将 access/refresh token 存入 tokenStore，transport 自动附带
 *   Authorization 头（401 时单次 refresh 重试）。
 * - 账号由运维通过 `manage.py create_admin` 创建（公开注册默认关闭）。
 * - 匿名用户不受影响：所有原有匿名功能照旧，只是数据源管理等写操作
 *   需要登录。
 */

function useAuthUser(): AuthUser | null {
  return useSyncExternalStore(
    subscribeAuth,
    getAuthUser,
    () => null,
  );
}

export function AccountSection() {
  const user = useAuthUser();
  const [identifier, setIdentifier] = useState('');
  const [password, setPassword] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    // Sign-in state changes clear stale form errors.
    setError(null);
  }, [user]);

  const handleLogin = useCallback(
    async (e: React.FormEvent) => {
      e.preventDefault();
      if (busy) return;
      const trimmed = identifier.trim();
      if (!trimmed || !password) {
        setError('请输入用户名/邮箱和密码');
        return;
      }
      setBusy(true);
      setError(null);
      try {
        const res = await login(trimmed, password);
        setAuth(
          { accessToken: res.access_token, refreshToken: res.refresh_token ?? null },
          res.user,
        );
        setPassword('');
        setIdentifier('');
      } catch (err) {
        const detail =
          (err as { body?: { detail?: string } })?.body?.detail ??
          (err instanceof Error ? err.message : '登录失败');
        setError(String(detail));
      } finally {
        setBusy(false);
      }
    },
    [busy, identifier, password],
  );

  const handleLogout = useCallback(async () => {
    if (busy) return;
    setBusy(true);
    try {
      await logout();
    } catch {
      // logout() already cleared local credentials in its finally block —
      // a failed server call (offline / already-revoked token) must not keep
      // the user signed in locally.
    } finally {
      setBusy(false);
    }
  }, [busy]);

  return (
    <div className="flex flex-col gap-5">
      <STitle title="账户" sub="Account" />

      {user ? (
        <div className="rounded-md border border-edge-subtle bg-surface-raised px-4 py-3">
          <div className="flex items-center justify-between gap-3">
            <div>
              <div className="text-title font-semibold text-ink">{user.username}</div>
              <div className="text-body text-ink-muted">
                {user.email ?? ''}
                {user.role ? ` · ${user.role}` : ''}
              </div>
            </div>
            <button
              onClick={handleLogout}
              disabled={busy}
              className="rounded-md border border-edge-subtle bg-surface-raised px-3 py-1.5 text-body font-medium text-ink-secondary transition-colors hover:bg-surface-hover disabled:opacity-50"
            >
              退出登录
            </button>
          </div>
          <div className="mt-2 text-body text-ink-muted">
            已登录：数据源管理等需要认证的操作现在可用。
          </div>
        </div>
      ) : (
        <form onSubmit={handleLogin} className="flex flex-col gap-3" aria-label="登录">
          <div>
            <label
              htmlFor="auth-identifier"
              className="mb-1 block text-body font-medium text-ink-secondary"
            >
              用户名 / 邮箱
            </label>
            <input
              id="auth-identifier"
              autoComplete="username"
              value={identifier}
              onChange={(e) => setIdentifier(e.target.value)}
              className="w-full rounded-md border border-edge-subtle bg-surface-sunken px-3 py-2 text-body text-ink outline-none focus:border-[var(--agent-accent)]"
            />
          </div>
          <div>
            <label
              htmlFor="auth-password"
              className="mb-1 block text-body font-medium text-ink-secondary"
            >
              密码
            </label>
            <input
              id="auth-password"
              type="password"
              autoComplete="current-password"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              className="w-full rounded-md border border-edge-subtle bg-surface-sunken px-3 py-2 text-body text-ink outline-none focus:border-[var(--agent-accent)]"
            />
          </div>
          {error && (
            <div role="alert" className="text-body text-red-500">
              {error}
            </div>
          )}
          <button
            type="submit"
            disabled={busy}
            className="rounded-md px-3 py-2 text-body font-semibold text-ink-on-accent disabled:opacity-50"
            style={{ backgroundColor: 'var(--agent-accent, #16a34a)' }}
          >
            {busy ? '登录中…' : '登录'}
          </button>
          <div className="text-body text-ink-muted">
            账号由运维通过 <code>manage.py create_admin</code> 创建（公开注册默认关闭）。
            匿名使用不受影响；数据源管理等写操作需要登录。
          </div>
        </form>
      )}
    </div>
  );
}
