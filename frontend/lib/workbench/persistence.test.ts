/**
 * Workbench doc 持久化（W3）行为测试。
 *
 * 锁定三条不变式：
 *   P1. armed 前（恢复完成前 / 新会话无 sid）绝不提交 —— 防恢复竞态把空
 *       doc 盖掉新会话的服务器端 doc；
 *   P2. 恢复（hydrateWorkbenchFromSpec）→ 水合 store + 武装基线（同 doc
 *       不再触发提交）；组织态编辑 → 防抖提交 patch_workbench_state；
 *   P3. 提交走既有 commitMapSpecMutation 通道（CAS 串行链，无第二真相）。
 */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { useHudStore } from '@/lib/store/useHudStore';
import {
  getMapSpecSessionCursor,
  resetLiveState,
  setMapSpecSessionCursor,
} from '@/lib/mapspec/session-cursor';
import {
  hydrateWorkbenchFromSpec,
  markWorkbenchHydrated,
  notifyWorkbenchSessionChanged,
  startWorkbenchPersistence,
  stopWorkbenchPersistence,
  workbenchPersistenceArmed,
} from './persistence';

const commitSpy = vi.fn();

vi.mock('@/lib/mapspec/user-mutation', () => ({
  commitMapSpecMutation: (...args: unknown[]) => commitSpy(...args),
}));

function tickDebounce(ms = 900): Promise<void> {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

describe('workbench persistence（W3）', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    resetLiveState();
    stopWorkbenchPersistence();
    commitSpy.mockResolvedValue({ mutation_revision: 1 });
    useHudStore.setState({
      layerGroups: [],
      layerGroupMembership: {},
      lockedLayerIds: [],
    });
    startWorkbenchPersistence();
  });

  afterEach(() => {
    stopWorkbenchPersistence();
    vi.useRealTimers();
  });

  it('P1: 未武装时不提交（无会话 / 切换后未恢复）', async () => {
    expect(workbenchPersistenceArmed()).toBe(false);
    useHudStore.getState().createLayerGroup('A');
    await tickDebounce();
    expect(commitSpy).not.toHaveBeenCalled();
  });

  it('P2: 恢复 → 水合 + 武装；同基线编辑外才提交', async () => {
    setMapSpecSessionCursor('sess-wb', 3);
    notifyWorkbenchSessionChanged('sess-wb');
    hydrateWorkbenchFromSpec({
      workbench: {
        version: 5,
        groups: [{ id: 'g1', name: '恢复组', collapsed: false, parentId: null }],
        membership: { l1: 'g1' },
        lockedLayerIds: ['l2'],
        mode: 'compose',
      },
    });
    expect(workbenchPersistenceArmed()).toBe(true);
    // 水合生效
    const s = useHudStore.getState();
    expect(s.layerGroups).toHaveLength(1);
    expect(s.layerGroupMembership).toEqual({ l1: 'g1' });
    expect(s.lockedLayerIds).toEqual(['l2']);
    // 基线内编辑（同 doc）不提交
    await tickDebounce();
    expect(commitSpy).not.toHaveBeenCalled();
    // 组织态编辑 → 防抖提交 patch_workbench_state
    useHudStore.getState().createLayerGroup('新组');
    await tickDebounce();
    expect(commitSpy).toHaveBeenCalledTimes(1);
    const body = commitSpy.mock.calls[0][0] as { intent: string; doc: { version: number; groups: unknown[] } };
    expect(body.intent).toBe('patch_workbench_state');
    expect(body.doc.version).toBe(5);
    expect(body.doc.groups).toHaveLength(2);
  });

  it('P2b: 旧会话（无 workbench 分支）空基线起跑', async () => {
    setMapSpecSessionCursor('sess-old', 1);
    notifyWorkbenchSessionChanged('sess-old');
    hydrateWorkbenchFromSpec(undefined);
    expect(workbenchPersistenceArmed()).toBe(true);
    useHudStore.getState().toggleLayerLocked('l9');
    await tickDebounce();
    expect(commitSpy).toHaveBeenCalledTimes(1);
  });

  it('R1-C1: 409 superseded 不算提交成功 —— 不写基线/不广播，回灌服务端真相', async () => {
    setMapSpecSessionCursor('sess-sup', 1);
    notifyWorkbenchSessionChanged('sess-sup');
    hydrateWorkbenchFromSpec(undefined);
    // 首次提交被 CAS 拒绝：返回 superseded + 服务端已收敛的 workbench 分支
    commitSpy.mockResolvedValueOnce({
      status: 'superseded',
      mutation_revision: 9,
      mapspec: {
        workbench: {
          version: 5,
          groups: [{ id: 'server-g', name: '服务端组', collapsed: false, parentId: null }],
          membership: {},
          lockedLayerIds: [],
          mode: 'explore',
        },
      },
    });
    useHudStore.getState().createLayerGroup('本地未落盘组');
    await tickDebounce();
    expect(commitSpy).toHaveBeenCalledTimes(1);
    // 服务端真相回灌（本地被拒版本不得残留）
    const s = useHudStore.getState();
    expect(s.layerGroups).toHaveLength(1);
    expect(s.layerGroups[0].name).toBe('服务端组');
    // superseded 响应的 revision 已收敛
    expect(getMapSpecSessionCursor().revision).toBe(9);
  });

  it('P3: 会话切换重置武装 —— 排队提交作废、跨会话不写', async () => {
    setMapSpecSessionCursor('sess-a', 1);
    notifyWorkbenchSessionChanged('sess-a');
    markWorkbenchHydrated();
    useHudStore.getState().createLayerGroup('A 会话的组');
    // 提交前排入会话切换（防抖窗口内）
    notifyWorkbenchSessionChanged('sess-b');
    setMapSpecSessionCursor('sess-b', 0);
    await tickDebounce();
    expect(commitSpy).not.toHaveBeenCalled();
    // sess-b 恢复完成前的编辑也不提交
    useHudStore.getState().createLayerGroup('B 恢复前');
    await tickDebounce();
    expect(commitSpy).not.toHaveBeenCalled();
    expect(getMapSpecSessionCursor().sessionId).toBe('sess-b');
  });
});
