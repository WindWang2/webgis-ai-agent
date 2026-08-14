import { describe, it, expect, vi, beforeAll, beforeEach } from 'vitest';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import { installInMemoryLocalStorage } from '../../test/in-memory-local-storage';

/**
 * Round-2 auth wiring: data-fabric write endpoints require Bearer auth, so
 * the settings panel gains an account section for login/logout. These tests
 * pin the UI contract: sign-in persists tokens, failures surface the backend
 * detail, sign-out clears state even when the server call fails.
 */

vi.mock('@/lib/api/auth', () => ({
  login: vi.fn(),
  logout: vi.fn(),
}));

import { AccountSection } from './account-section';
import { login, logout } from '@/lib/api/auth';
import { getAccessToken, getAuthUser, setAuth, clearAuth } from '@/lib/auth/tokenStore';

const mockLogin = vi.mocked(login);
const mockLogout = vi.mocked(logout);

beforeAll(() => {
  installInMemoryLocalStorage();
});

beforeEach(() => {
  vi.clearAllMocks();
  window.localStorage.clear();
  clearAuth();
});

describe('AccountSection', () => {
  it('shows the login form when signed out', () => {
    render(<AccountSection />);
    expect(screen.getByLabelText('用户名 / 邮箱')).toBeTruthy();
    expect(screen.getByLabelText('密码')).toBeTruthy();
    expect(screen.getByRole('button', { name: '登录' })).toBeTruthy();
  });

  it('signs in: persists tokens and switches to the signed-in view', async () => {
    mockLogin.mockResolvedValue({
      access_token: 'acc-1',
      refresh_token: 'ref-1',
      token_type: 'bearer',
      expires_in: 1800,
      user: { id: 'u1', username: 'ops', role: 'admin' },
    });
    render(<AccountSection />);

    fireEvent.change(screen.getByLabelText('用户名 / 邮箱'), {
      target: { value: 'ops' },
    });
    fireEvent.change(screen.getByLabelText('密码'), {
      target: { value: 'secret123' },
    });
    fireEvent.click(screen.getByRole('button', { name: '登录' }));

    await waitFor(() => {
      expect(screen.getByText('ops')).toBeTruthy();
    });
    expect(mockLogin).toHaveBeenCalledWith('ops', 'secret123');
    expect(getAccessToken()).toBe('acc-1');
    expect(getAuthUser()?.username).toBe('ops');
    expect(screen.getByRole('button', { name: '退出登录' })).toBeTruthy();
  });

  it('surfaces the backend error detail on failed login', async () => {
    mockLogin.mockRejectedValue(
      Object.assign(new Error('login failed'), {
        body: { detail: '用户名或密码错误' },
      }),
    );
    render(<AccountSection />);

    fireEvent.change(screen.getByLabelText('用户名 / 邮箱'), {
      target: { value: 'ops' },
    });
    fireEvent.change(screen.getByLabelText('密码'), {
      target: { value: 'wrong' },
    });
    fireEvent.click(screen.getByRole('button', { name: '登录' }));

    await waitFor(() => {
      expect(screen.getByRole('alert').textContent).toContain('用户名或密码错误');
    });
    expect(getAccessToken()).toBeNull();
    // Still on the login form.
    expect(screen.getByLabelText('用户名 / 邮箱')).toBeTruthy();
  });

  it('requires both fields before calling the API', async () => {
    render(<AccountSection />);
    fireEvent.click(screen.getByRole('button', { name: '登录' }));
    await waitFor(() => {
      expect(screen.getByRole('alert').textContent).toContain('请输入');
    });
    expect(mockLogin).not.toHaveBeenCalled();
  });

  it('signs out: clears local state even when the server logout fails', async () => {
    setAuth({ accessToken: 'acc-1', refreshToken: 'ref-1' }, { id: 'u1', username: 'ops' });
    // Server logout fails AND the local clear (owned by the real logout)
    // still runs — replicate the real contract on the mock.
    mockLogout.mockImplementation(async () => {
      clearAuth();
      throw new TypeError('offline');
    });

    render(<AccountSection />);
    fireEvent.click(screen.getByRole('button', { name: '退出登录' }));

    await waitFor(() => {
      expect(screen.getByLabelText('用户名 / 邮箱')).toBeTruthy();
    });
    expect(getAccessToken()).toBeNull();
  });
});
