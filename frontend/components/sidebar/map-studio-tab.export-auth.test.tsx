import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { render, screen, fireEvent, act } from '@testing-library/react';

/**
 * #469 — 导出按钮的登录门控。
 *
 * POST /api/v1/export 需要认证（map.py get_current_user），但「发布并导出」
 * 按钮从不检查登录态：匿名（默认模式）用户每次点击都会 401，且
 * runExport 捕获后仅返回 {ok:false}，命令 settled failed，用户看不到任何
 * 提示 —— 导出功能在默认模式下静默失效。
 *
 * 契约：
 *  - 匿名：按钮 disabled + 可见的登录提示文案；点击不派发 export_map。
 *  - 已登录：按钮可用，点击派发 export_map。
 *  - 登录/登出状态变化时按钮随之切换（subscribeAuth 通知）。
 */
const dispatchAction = vi.fn();

const mockState: Record<string, any> = {
  exportSettings: {
    isExportMode: false,
    title: '',
    subtitle: '',
    author: '',
    dataSource: '',
    showWatermark: true,
    showCompass: true,
    showScale: true,
    showLegend: true,
    showMetadata: true,
    showGraticules: false,
    paperSize: 'screen',
    orientation: 'landscape',
    dpi: 96,
    format: 'png',
  },
  updateExportSettings: vi.fn(),
  exports: [],
  setExports: vi.fn(),
  leftPanelOpen: true,
};

vi.mock('@/lib/store/useHudStore', () => ({
  useHudStore: (selector: (s: any) => any) => selector(mockState),
}));

vi.mock('@/lib/contexts/map-action-context', () => ({
  useMapAction: () => ({ dispatchAction }),
}));

// Auth store mock: mutable current user + a real listener set so
// useSyncExternalStore reacts to sign-in/out like production.
let mockUser: { id: string; username: string } | null = null;
const authListeners = new Set<() => void>();
vi.mock('@/lib/auth/tokenStore', () => ({
  getAuthUser: () => mockUser,
  subscribeAuth: (fn: () => void) => {
    authListeners.add(fn);
    return () => authListeners.delete(fn);
  },
}));

import { MapStudioTab } from './map-studio-tab';

const signedInUser = { id: 'u1', username: 'ops' };

function signIn() {
  mockUser = signedInUser;
  act(() => authListeners.forEach((fn) => fn()));
}

function signOut() {
  mockUser = null;
  act(() => authListeners.forEach((fn) => fn()));
}

beforeEach(() => {
  vi.clearAllMocks();
  mockState.leftPanelOpen = true;
  mockState.exports = [];
  mockUser = null;
});

afterEach(() => {
  signOut();
});

describe('MapStudioTab — 导出按钮登录门控 (#469)', () => {
  it('匿名时按钮 disabled 且展示登录提示，点击不派发 export_map', () => {
    render(<MapStudioTab />);

    const btn = screen.getByRole('button', { name: /发布并导出|登录后可导出/ });
    expect(btn).toBeDisabled();
    // 登录引导必须可见（不是只有 title tooltip）
    expect(screen.getByText(/设置 → 账户 登录/)).toBeInTheDocument();

    fireEvent.click(btn);
    expect(dispatchAction).not.toHaveBeenCalled();
  });

  it('已登录时按钮可用，点击派发 export_map 及当前导出设置', () => {
    render(<MapStudioTab />);
    signIn();

    const btn = screen.getByRole('button', { name: /发布并导出/ });
    expect(btn).toBeEnabled();

    fireEvent.click(btn);
    expect(dispatchAction).toHaveBeenCalledTimes(1);
    expect(dispatchAction).toHaveBeenCalledWith({
      command: 'export_map',
      params: expect.objectContaining({ format: 'png' }),
    });
  });

  it('登录 → 登出状态切换时按钮随之启用/禁用', () => {
    render(<MapStudioTab />);

    let btn = screen.getByRole('button', { name: /发布并导出|登录后可导出/ });
    expect(btn).toBeDisabled();

    signIn();
    btn = screen.getByRole('button', { name: /发布并导出/ });
    expect(btn).toBeEnabled();

    signOut();
    btn = screen.getByRole('button', { name: /发布并导出|登录后可导出/ });
    expect(btn).toBeDisabled();
  });
});
