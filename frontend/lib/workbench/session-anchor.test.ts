/**
 * 刷新恢复/会话锚（W11）行为测试。
 *
 * 锁定四条不变式：
 *   R1. 锚只存指针（sessionId/authed/savedAt）—— 不存工作台内容（无第二
 *       事实源）；
 *   R2. 认证会话可恢复判定成立；匿名会话与登出态不自动恢复（不新增凭据
 *       持久化面）；
 *   R3. 新会话语义清锚；
 *   R4. 脏 doc 在 pagehide 冲刷（flushWorkbenchDoc 发起即时提交）。
 */
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { useHudStore } from '@/lib/store/useHudStore';
import { resetLiveState, setMapSpecSessionCursor } from '@/lib/mapspec/session-cursor';
import {
  clearSessionAnchor,
  readSessionAnchor,
  restorableSessionAnchor,
  writeSessionAnchor,
} from './session-anchor';
import {
  flushWorkbenchDoc,
  hydrateWorkbenchFromSpec,
  notifyWorkbenchSessionChanged,
  startWorkbenchPersistence,
  stopWorkbenchPersistence,
} from './persistence';

const commitSpy = vi.fn().mockResolvedValue({ mutation_revision: 2 });
vi.mock('@/lib/mapspec/user-mutation', () => ({
  commitMapSpecMutation: (...args: unknown[]) => commitSpy(...args),
}));

const authState = vi.hoisted(() => ({ token: null as string | null }));
vi.mock('@/lib/auth/tokenStore', () => ({
  getAccessToken: () => authState.token,
}));

// 全局 setup 把 localStorage mock 成空 vi.fn —— 这里接上内存 backing store。
const backing = new Map<string, string>();
function useMemoryStorage(): void {
  vi.mocked(window.localStorage.getItem).mockImplementation(
    (k: string) => backing.get(k) ?? null,
  );
  vi.mocked(window.localStorage.setItem).mockImplementation((k: string, v: string) => {
    backing.set(k, v);
  });
  vi.mocked(window.localStorage.removeItem).mockImplementation((k: string) => {
    backing.delete(k);
  });
  backing.clear();
}

describe('session anchor（W11）', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    resetLiveState();
    clearSessionAnchor();
    authState.token = null;
    useMemoryStorage();
  });

  it('R1: 锚只存指针字段', () => {
    authState.token = 'jwt-token';
    writeSessionAnchor('sess-1');
    const anchor = readSessionAnchor();
    expect(anchor).toEqual({
      sessionId: 'sess-1',
      authed: true,
      savedAt: expect.any(Number),
    });
    // 损坏 payload → null（防御解析）
    window.localStorage.setItem('wb5:session-anchor', '{oops');
    expect(readSessionAnchor()).toBeNull();
    window.localStorage.setItem('wb5:session-anchor', JSON.stringify({ foo: 1 }));
    expect(readSessionAnchor()).toBeNull();
  });
  it('R2: 认证 + 持有凭据 → 可恢复；匿名/登出 → 不恢复', () => {
    authState.token = 'jwt-token';
    writeSessionAnchor('sess-auth');
    expect(restorableSessionAnchor()?.sessionId).toBe('sess-auth');

    // 登出（token 清除）
    authState.token = null;
    expect(restorableSessionAnchor()).toBeNull();

    // 匿名会话锚（authed=false）
    writeSessionAnchor('sess-anon');
    expect(readSessionAnchor()?.authed).toBe(false);
    expect(restorableSessionAnchor()).toBeNull();
  });

  it('R3: clearSessionAnchor 清除指针', () => {
    authState.token = 'jwt-token';
    writeSessionAnchor('sess-1');
    expect(readSessionAnchor()).not.toBeNull();
    clearSessionAnchor();
    expect(readSessionAnchor()).toBeNull();
  });
});

describe('flushWorkbenchDoc（W11 pagehide 冲刷）', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    resetLiveState();
    stopWorkbenchPersistence();
    commitSpy.mockResolvedValue({ mutation_revision: 2 });
    useHudStore.setState({ layerGroups: [], layerGroupMembership: {}, lockedLayerIds: [] });
  });

  afterEach(() => {
    stopWorkbenchPersistence();
  });

  it('R4: 脏 doc 立即提交（不等防抖窗口）', async () => {
    setMapSpecSessionCursor('sess-flush', 1);
    notifyWorkbenchSessionChanged('sess-flush');
    hydrateWorkbenchFromSpec(undefined);
    startWorkbenchPersistence();
    useHudStore.getState().createLayerGroup('未落盘的组');
    flushWorkbenchDoc();
    await vi.waitFor(() => {
      expect(commitSpy).toHaveBeenCalledTimes(1);
    });
    const body = commitSpy.mock.calls[0][0] as { intent: string };
    // V6：冲刷同样走 delta 优先通道（flush 只是不等防抖窗口，不改提交语义）
    expect(body.intent).toBe('patch_workbench_delta');
  });

  it('R4b: 无脏变更时 flush 不提交', () => {
    setMapSpecSessionCursor('sess-clean', 1);
    notifyWorkbenchSessionChanged('sess-clean');
    hydrateWorkbenchFromSpec(undefined);
    startWorkbenchPersistence();
    flushWorkbenchDoc();
    expect(commitSpy).not.toHaveBeenCalled();
  });
});
