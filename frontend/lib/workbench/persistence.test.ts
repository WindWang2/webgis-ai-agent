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
    // 组织态编辑 → 防抖提交（V6：结构化 delta 优先）
    useHudStore.getState().createLayerGroup('新组');
    await tickDebounce();
    expect(commitSpy).toHaveBeenCalledTimes(1);
    const body = commitSpy.mock.calls[0][0] as {
      intent: string; doc?: { version: number; groups: unknown[] };
      delta?: { setGroups?: Array<{ id: string; name?: string }> };
    };
    expect(body.intent).toBe('patch_workbench_delta');
    expect(body.doc).toBeUndefined();
    expect(body.delta?.setGroups).toHaveLength(1);
    expect(body.delta?.setGroups?.[0].name).toBe('新组');
    expect(body.delta?.setGroups?.[0].id).toBeTypeOf('string');
  });

  it('V6: diff 不可表达（mode 变更）→ 回退全量 patch_workbench_state + base_workbench_revision', async () => {
    setMapSpecSessionCursor('sess-mode', 2);
    notifyWorkbenchSessionChanged('sess-mode');
    hydrateWorkbenchFromSpec({
      workbench: {
        version: 5,
        groups: [{ id: 'g1', name: 'A', collapsed: false, parentId: null }],
        membership: {},
        lockedLayerIds: [],
        mode: 'explore',
        _rev: 7, // 服务端 V6 盖章
      },
    });
    // mode 变更不在 delta 域 → 全量回退
    useHudStore.getState().setWorkbenchMode('analyze', 'user');
    await tickDebounce();
    const body = commitSpy.mock.calls[0][0] as {
      intent: string; doc: { mode: string };
      base_workbench_revision?: number;
    };
    expect(body.intent).toBe('patch_workbench_state');
    expect(body.doc.mode).toBe('analyze');
    // V6：全量携带 workbench 级 CAS 锚点 = 服务端 doc 的 _rev 盖章
    expect(body.base_workbench_revision).toBe(7);
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

  it('V6 R1-M5: 409 → 回灌服务端真相 + 在飞 delta 单次 rebase（bounded）', async () => {
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
    await tickDebounce(); // 第一窗口：提交被拒
    await tickDebounce(); // rebase 重提交经新防抖窗口
    // 第一次 = 被拒提交；rebase 后自动重提交一次（bounded 单次）
    expect(commitSpy.mock.calls.length).toBeGreaterThanOrEqual(2);
    // 服务端真相曾作为基线回灌（store 在 rebase 窗口 = 服务端 doc + 本地 delta）
    // superseded 响应的 revision 已收敛
    expect(getMapSpecSessionCursor().revision).toBe(9);
    // rebase 的重提交仍是 delta intent（不是整表回写）
    const second = commitSpy.mock.calls[1][0] as { intent: string };
    expect(second.intent).toBe('patch_workbench_delta');
  });

  it('V6 R1-M5b: 会话切换后迟到的 409 响应不得把旧会话 doc 写进新会话', async () => {
    setMapSpecSessionCursor('sess-late-a', 1);
    notifyWorkbenchSessionChanged('sess-late-a');
    hydrateWorkbenchFromSpec(undefined);
    // 首次提交挂起：响应回来之前切换会话
    let resolveCommit: (value: unknown) => void = () => {};
    commitSpy.mockImplementationOnce(
      () => new Promise((resolve) => { resolveCommit = resolve; }),
    );
    useHudStore.getState().createLayerGroup('旧会话组');
    await tickDebounce();
    expect(commitSpy).toHaveBeenCalledTimes(1);

    // 切到新会话并完成恢复（重新武装）
    notifyWorkbenchSessionChanged('sess-late-b');
    setMapSpecSessionCursor('sess-late-b', 0);
    hydrateWorkbenchFromSpec({
      workbench: {
        version: 5,
        groups: [{ id: 'gb', name: '新会话组', collapsed: false, parentId: null }],
        membership: {},
        lockedLayerIds: [],
        mode: 'explore',
      },
    });

    // 迟到响应：旧会话的 superseded（携带旧会话服务端 doc）
    resolveCommit({
      status: 'superseded',
      mutation_revision: 9,
      mapspec: {
        workbench: {
          version: 5,
          groups: [{ id: 'ga', name: '旧会话服务端组', collapsed: false, parentId: null }],
          membership: {},
          lockedLayerIds: [],
          mode: 'explore',
        },
      },
    });
    await tickDebounce(); // 覆盖 rebase 重提交的防抖窗口
    // 新会话 store 未被旧会话服务端 doc 水合覆盖
    expect(useHudStore.getState().layerGroups.map((g) => g.name)).toEqual(['新会话组']);
    // 旧会话编辑未被 rebase 提交进新会话
    expect(commitSpy).toHaveBeenCalledTimes(1);
  });

  it('V6 R1-M5c: 迟到成功响应不得把旧会话 doc 写成新会话的提交基线', async () => {
    setMapSpecSessionCursor('sess-late2-a', 1);
    notifyWorkbenchSessionChanged('sess-late2-a');
    hydrateWorkbenchFromSpec(undefined);
    let resolveCommit: (value: unknown) => void = () => {};
    commitSpy.mockImplementationOnce(
      () => new Promise((resolve) => { resolveCommit = resolve; }),
    );
    const staleGroupId = useHudStore.getState().createLayerGroup('旧会话组');
    await tickDebounce();
    expect(commitSpy).toHaveBeenCalledTimes(1);

    notifyWorkbenchSessionChanged('sess-late2-b');
    setMapSpecSessionCursor('sess-late2-b', 0);
    hydrateWorkbenchFromSpec({
      workbench: {
        version: 5,
        groups: [{ id: 'gb2', name: '新会话组', collapsed: false, parentId: null }],
        membership: {},
        lockedLayerIds: [],
        mode: 'explore',
      },
    });
    resolveCommit({
      mutation_revision: 5,
      mapspec: {
        workbench: {
          version: 5,
          groups: [{ id: staleGroupId, name: '旧会话组', collapsed: false, parentId: null }],
          membership: {},
          lockedLayerIds: [],
          mode: 'explore',
          _rev: 5,
        },
      },
    });
    await tickDebounce();
    // 新会话的编辑 → 提交 delta 只描述新会话变更（不夹带旧会话组的删除）
    useHudStore.getState().createLayerGroup('新会话新组');
    await tickDebounce();
    expect(commitSpy).toHaveBeenCalledTimes(2);
    const body = commitSpy.mock.calls[1][0] as {
      intent: string;
      delta?: { setGroups?: Array<{ id: string }>; removeGroupIds?: string[] };
    };
    expect(body.intent).toBe('patch_workbench_delta');
    expect(body.delta?.removeGroupIds).toBeUndefined();
    expect(body.delta?.setGroups).toHaveLength(1);
  });

  it('V6 R1-M4b: delta 通道 409 rebase 保留提交在飞期间的新编辑', async () => {
    setMapSpecSessionCursor('sess-delta-late', 1);
    notifyWorkbenchSessionChanged('sess-delta-late');
    hydrateWorkbenchFromSpec({
      workbench: {
        version: 5,
        groups: [{ id: 'g0', name: '基线组', collapsed: false, parentId: null }],
        membership: {},
        lockedLayerIds: [],
        mode: 'explore',
      },
    });
    let resolveCommit: (value: unknown) => void = () => {};
    commitSpy.mockImplementationOnce(
      () => new Promise((resolve) => { resolveCommit = resolve; }),
    );
    useHudStore.getState().renameLayerGroup('g0', '在飞编辑');
    await tickDebounce();
    expect(commitSpy).toHaveBeenCalledTimes(1);
    // 提交在飞期间用户的新编辑（本地脏态）
    useHudStore.getState().toggleLayerLocked('layer-late');
    // 409：服务端已收敛到另一份 doc
    resolveCommit({
      status: 'superseded',
      mutation_revision: 12,
      mapspec: {
        workbench: {
          version: 5,
          groups: [{ id: 'gs', name: '服务端组', collapsed: false, parentId: null }],
          membership: {},
          lockedLayerIds: [],
          mode: 'explore',
        },
      },
    });
    await tickDebounce(); // rebase 重提交经新防抖窗口
    // 在飞期间的新编辑未被静默丢弃：store 保留 + 随 rebase 一并落库
    expect(useHudStore.getState().lockedLayerIds).toContain('layer-late');
    expect(commitSpy).toHaveBeenCalledTimes(2);
    const second = commitSpy.mock.calls[1][0] as {
      intent: string;
      delta?: { locksAdd?: string[] };
    };
    expect(second.intent).toBe('patch_workbench_delta');
    expect(second.delta?.locksAdd).toContain('layer-late');
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
