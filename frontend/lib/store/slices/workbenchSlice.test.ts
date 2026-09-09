/**
 * Workbench V4（Wave 1）：统一工作区投影 slice 契约测试。
 * 锁定：模式切换语义、多选、分组树生命周期（prune/reset）、对比状态。
 */
import { describe, it, expect, beforeEach } from 'vitest';
import { createStore } from 'zustand/vanilla';
import { createWorkbenchSlice, MODE_TABS } from './workbenchSlice';
import type { HudState } from '../hud-types';

type Store = {
  getState: () => HudState;
  setState: (partial: Partial<HudState>) => void;
};

function makeStore(): Store {
  const store = createStore<any>()((...args) => ({
    ...createWorkbenchSlice(args[0] as any, args[1] as any, args[2] as any),
  }));
  return store as unknown as Store;
}

describe('workbenchSlice · mode', () => {
  it('缺省 explore；切换保留每模式记忆的 active tab', () => {
    const store = makeStore();
    expect(store.getState().mode).toBe('explore');
    store.getState().setWorkbenchMode('analyze');
    expect(store.getState().mode).toBe('analyze');
    expect(store.getState().modeOrigin).toBe('user');
  });

  it('同模式重复切换不产生新状态对象（no-op 门）', () => {
    const store = makeStore();
    store.getState().setWorkbenchMode('explore');
    const before = store.getState();
    store.getState().setWorkbenchMode('explore');
    expect(store.getState()).toBe(before);
  });

  it('agent 切换记录 modeOrigin=agent（UI 据此展示回执）', () => {
    const store = makeStore();
    store.getState().setWorkbenchMode('compose', 'agent');
    expect(store.getState().modeOrigin).toBe('agent');
  });
});

describe('workbenchSlice · layer selection', () => {
  it('toggle 去重添加/移除', () => {
    const store = makeStore();
    store.getState().toggleLayerSelected('a');
    store.getState().toggleLayerSelected('b');
    expect(store.getState().selectedLayerIds).toEqual(['a', 'b']);
    store.getState().toggleLayerSelected('a');
    expect(store.getState().selectedLayerIds).toEqual(['b']);
  });

  it('setSelectedLayerIds 幂等去重', () => {
    const store = makeStore();
    store.getState().setSelectedLayerIds(['a', 'a', 'b']);
    expect(store.getState().selectedLayerIds).toEqual(['a', 'b']);
    store.getState().clearLayerSelection();
    expect(store.getState().selectedLayerIds).toEqual([]);
  });
});

describe('workbenchSlice · layer groups', () => {
  it('create/rename/remove 生命周期；remove 释放成员关系', () => {
    const store = makeStore();
    const gid = store.getState().createLayerGroup('东部城市群');
    expect(store.getState().layerGroups).toEqual([
      { id: gid, name: '东部城市群', collapsed: false, parentId: null },
    ]);
    store.getState().assignLayersToGroup(['l1', 'l2'], gid);
    expect(store.getState().layerGroupMembership).toEqual({ l1: gid, l2: gid });

    store.getState().renameLayerGroup(gid, '长三角');
    expect(store.getState().layerGroups[0].name).toBe('长三角');

    store.getState().removeLayerGroup(gid);
    expect(store.getState().layerGroups).toEqual([]);
    expect(store.getState().layerGroupMembership).toEqual({});
  });

  it('create 支持嵌套父；未知父落根', () => {
    const store = makeStore();
    const parent = store.getState().createLayerGroup('父组');
    const child = store.getState().createLayerGroup('子组', parent);
    expect(store.getState().layerGroups.find((g) => g.id === child)?.parentId).toBe(parent);
    const orphan = store.getState().createLayerGroup('孤儿', 'wg-ghost');
    expect(store.getState().layerGroups.find((g) => g.id === orphan)?.parentId).toBeNull();
  });

  it('moveLayerGroup：合法嵌套生效；环/未知父被拒绝并保持原状', () => {
    const store = makeStore();
    const a = store.getState().createLayerGroup('A');
    const b = store.getState().createLayerGroup('B');
    // B 移到 A 下
    expect(store.getState().moveLayerGroup(b, a)).toBe(true);
    expect(store.getState().layerGroups.find((g) => g.id === b)?.parentId).toBe(a);
    // A 移到自己的子孙 B 下 → 成环，拒绝
    expect(store.getState().moveLayerGroup(a, b)).toBe(false);
    expect(store.getState().layerGroups.find((g) => g.id === a)?.parentId).toBeNull();
    // 未知父 → 拒绝
    expect(store.getState().moveLayerGroup(a, 'wg-ghost')).toBe(false);
    // 提为根
    expect(store.getState().moveLayerGroup(b, null)).toBe(true);
    expect(store.getState().layerGroups.find((g) => g.id === b)?.parentId).toBeNull();
  });

  it('removeLayerGroup 嵌套语义：子组提升到被删组的父（不孤儿）', () => {
    const store = makeStore();
    const root = store.getState().createLayerGroup('根');
    const mid = store.getState().createLayerGroup('中', root);
    const leaf = store.getState().createLayerGroup('叶', mid);
    store.getState().removeLayerGroup(mid);
    expect(store.getState().layerGroups.find((g) => g.id === leaf)?.parentId).toBe(root);
    expect(store.getState().layerGroups.some((g) => g.id === mid)).toBe(false);
  });

  it('hydrateWorkbenchDoc：合法 doc 全量水合；非法拒绝', () => {
    const store = makeStore();
    const ok = store.getState().hydrateWorkbenchDoc({
      version: 5,
      groups: [{ id: 'g1', name: '恢复组', collapsed: true, parentId: null }],
      membership: { l1: 'g1' },
      lockedLayerIds: ['l2'],
      mode: 'explore',
    });
    expect(ok).toBe(true);
    const s = store.getState();
    expect(s.layerGroups).toEqual([{ id: 'g1', name: '恢复组', collapsed: true, parentId: null }]);
    expect(s.layerGroupMembership).toEqual({ l1: 'g1' });
    expect(s.lockedLayerIds).toEqual(['l2']);
    // null / 非 V5 → false 且状态不变
    expect(store.getState().hydrateWorkbenchDoc(null)).toBe(false);
  });

  it('assignLayersToGroup 拒绝未知组（no-op）；null 移出组', () => {
    const store = makeStore();
    const gid = store.getState().createLayerGroup('g');
    store.getState().assignLayersToGroup(['l1'], 'wg-999');
    expect(store.getState().layerGroupMembership).toEqual({});
    store.getState().assignLayersToGroup(['l1'], gid);
    store.getState().assignLayersToGroup(['l1'], null);
    expect(store.getState().layerGroupMembership).toEqual({});
  });

  it('toggleGroupCollapsed 翻转折叠', () => {
    const store = makeStore();
    const gid = store.getState().createLayerGroup('g');
    store.getState().toggleGroupCollapsed(gid);
    expect(store.getState().layerGroups[0].collapsed).toBe(true);
    store.getState().toggleGroupCollapsed(gid);
    expect(store.getState().layerGroups[0].collapsed).toBe(false);
  });

  it('pruneLayerGroups 只清 stale 成员，保留空组', () => {
    const store = makeStore();
    const gid = store.getState().createLayerGroup('g');
    store.getState().assignLayersToGroup(['keep', 'stale'], gid);
    store.getState().pruneLayerGroups(new Set(['keep']));
    expect(store.getState().layerGroupMembership).toEqual({ keep: gid });
    expect(store.getState().layerGroups).toHaveLength(1);
  });

  it('prune 无变化时不产生新 membership 引用（no-op 门）', () => {
    const store = makeStore();
    const gid = store.getState().createLayerGroup('g');
    store.getState().assignLayersToGroup(['a'], gid);
    const before = store.getState().layerGroupMembership;
    store.getState().pruneLayerGroups(new Set(['a']));
    expect(store.getState().layerGroupMembership).toBe(before);
  });

  it('resetLayerGroups 清组 + 多选（会话切换语义）', () => {
    const store = makeStore();
    const gid = store.getState().createLayerGroup('g');
    store.getState().assignLayersToGroup(['a'], gid);
    store.getState().toggleLayerSelected('a');
    store.getState().resetLayerGroups();
    expect(store.getState().layerGroups).toEqual([]);
    expect(store.getState().layerGroupMembership).toEqual({});
    expect(store.getState().selectedLayerIds).toEqual([]);
  });
});

describe('workbenchSlice · R1 行为锁定（review fixes）', () => {
  it('setWorkbenchMode 上收 tab 协调：写 activeLeftTab 为该模式记忆 tab', () => {
    const store = makeStore();
    store.setState({ modeActiveTab: { explore: 'chat', analyze: 'analysis', compose: 'components' } } as any);
    store.getState().setWorkbenchMode('compose');
    expect(store.getState().mode).toBe('compose');
    expect((store.getState() as any).activeLeftTab).toBe('components');
    expect((store.getState() as any).leftPanelOpen).toBe(true);
  });

  it('agent 切换记录 userModeBeforeAgent；用户切换不覆盖', () => {
    const store = makeStore();
    store.setState({ mode: 'compose' } as any);
    store.getState().setWorkbenchMode('analyze', 'agent');
    expect((store.getState() as any).userModeBeforeAgent).toBe('compose');
    store.getState().setWorkbenchMode('explore', 'user');
    // 用户切换不清 agent 前模式记录
    expect((store.getState() as any).userModeBeforeAgent).toBe('compose');
  });

  it('setSelectedLayerIds 等值 no-op（身份稳定）', () => {
    const store = makeStore();
    store.getState().setSelectedLayerIds(['a', 'b']);
    const before = store.getState().selectedLayerIds;
    store.getState().setSelectedLayerIds(['b', 'a']);
    expect(store.getState().selectedLayerIds).toBe(before);
  });

  it('resetLayerGroups 同时退出对比并清产物选择（位置保留）', () => {
    const store = makeStore();
    store.getState().enterComparison({ primaryLayerId: 'a', secondaryLayerId: 'b' });
    store.getState().updateComparison({ position: 0.42 });
    store.getState().setSelectedArtifactId('artifact-1');
    store.getState().resetLayerGroups();
    const s = store.getState() as any;
    expect(s.comparison.active).toBe(false);
    expect(s.comparison.position).toBe(0.42);
    expect(s.selectedArtifactId).toBeNull();
  });

  it('pruneLayerGroups 同步清理选择与锁定死 id', () => {
    const store = makeStore();
    store.getState().toggleLayerSelected('dead');
    store.getState().toggleLayerSelected('alive');
    store.getState().toggleLayerLocked('dead');
    store.getState().pruneLayerGroups(new Set(['alive']));
    const s = store.getState();
    expect(s.selectedLayerIds).toEqual(['alive']);
    expect(s.lockedLayerIds).toEqual([]);
  });
});

describe('workbenchSlice · comparison', () => {
  beforeEach(() => {
    // groupSeq 是模块级计数器，测试间不需要重置 —— id 唯一性已由 create 保证。
  });

  it('enter/update/exit；未激活时 update 是 no-op', () => {
    const store = makeStore();
    store.getState().enterComparison({ primaryLayerId: 'a', secondaryLayerId: 'b' });
    expect(store.getState().comparison.active).toBe(true);
    expect(store.getState().comparison.kind).toBe('swipe');
    expect(store.getState().comparison.syncPan).toBe(true);

    store.getState().updateComparison({ position: 0.3 });
    expect(store.getState().comparison.position).toBe(0.3);

    store.getState().exitComparison();
    expect(store.getState().comparison.active).toBe(false);
    // exit 保留分割位置（再次进入不跳回 0.5）。
    store.getState().enterComparison();
    expect(store.getState().comparison.position).toBe(0.3);

    store.getState().exitComparison();
    const inactive = store.getState().comparison;
    store.getState().updateComparison({ position: 0.9 });
    expect(store.getState().comparison).toBe(inactive);
  });

  it('exitComparison 后 selection 不自动清（独立子域，由调用方决定）', () => {
    const store = makeStore();
    store.getState().toggleLayerSelected('a');
    store.getState().exitComparison();
    expect(store.getState().selectedLayerIds).toEqual(['a']);
  });
});

describe('workbenchSlice · mode tabs vocabulary', () => {
  it('MODE_TABS 三模式组合互不覆盖且 chat 恒在场', () => {
    for (const tabs of Object.values(MODE_TABS)) {
      expect(tabs).toContain('chat');
    }
    expect(MODE_TABS.explore).toContain('layers');
    expect(MODE_TABS.analyze).toContain('analysis');
    expect(MODE_TABS.compose).toContain('export_layout');
  });
});
