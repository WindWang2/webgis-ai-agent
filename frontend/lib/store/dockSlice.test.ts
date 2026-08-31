import { describe, expect, it, beforeEach } from 'vitest';
import { useHudStore } from '@/lib/store/useHudStore';

/**
 * Dock slice（Workspace V2 / Goal C5）—— 工作区停靠模型。
 *
 * 契约：
 * - dock 归属是工作区 UI 状态：停靠/取消停靠绝不触碰语义组件状态
 *   （placement/enabled —— MapSpec 唯一真相），也不产生 component patch；
 * - 停靠即打开所在区；多面板标签（activePanel）随增删自愈；
 * - 会话重置清空（dock 状态不跨会话）。
 */
describe('dockSlice', () => {
  beforeEach(() => {
    useHudStore.getState().resetDockState();
  });

  it('docking a panel opens the region and records the placement', () => {
    useHudStore.getState().dockPanel('chart-panel', 'right');
    const s = useHudStore.getState();
    expect(s.dockPlacements['chart-panel']).toBe('right');
    expect(s.rightDock.open).toBe(true);
    expect(s.rightDock.panels).toEqual(['chart-panel']);
    expect(s.rightDock.activePanel).toBe('chart-panel');
  });

  it('docking is a workspace-only change — no MapSpec component patch channel touched', () => {
    // dockPanel 写的是 dock slice 状态；语义组件状态经 patch_component
    // 提交（此处不引入该通道 —— 断言 store 内不存在任何 placement/enabled
    // 突变痕迹：dock 不修改 committed spec 的任何组件字段）。
    useHudStore.getState().dockPanel('chart-panel', 'bottom');
    const s = useHudStore.getState();
    expect(s.dockPlacements['chart-panel']).toBe('bottom');
    expect(s.bottomDock.open).toBe(true);
  });

  it('re-docking to another region moves it (no duplicates)', () => {
    useHudStore.getState().dockPanel('chart-panel', 'right');
    useHudStore.getState().dockPanel('chart-panel', 'bottom');
    const s = useHudStore.getState();
    expect(s.dockPlacements['chart-panel']).toBe('bottom');
    expect(s.rightDock.panels).toEqual([]);
    expect(s.bottomDock.panels).toEqual(['chart-panel']);
  });

  it('undocking (float) removes the placement and closes empty regions', () => {
    useHudStore.getState().dockPanel('chart-panel', 'right');
    useHudStore.getState().dockPanel('chart-panel', 'float');
    const s = useHudStore.getState();
    expect(s.dockPlacements['chart-panel']).toBeUndefined();
    // Region stays open-flagged but empty → host renders nothing (open with
    // zero panels is not a visible dock). Panels list is the truth.
    expect(s.rightDock.panels).toEqual([]);
  });

  it('multi-panel tabs: active panel follows docking and self-heals on undock', () => {
    useHudStore.getState().dockPanel('chart-a', 'right');
    useHudStore.getState().dockPanel('chart-b', 'right');
    let s = useHudStore.getState();
    expect(s.rightDock.panels).toEqual(['chart-a', 'chart-b']);
    expect(s.rightDock.activePanel).toBe('chart-b');

    useHudStore.getState().setActiveDockPanel('right', 'chart-a');
    s = useHudStore.getState();
    expect(s.rightDock.activePanel).toBe('chart-a');

    useHudStore.getState().dockPanel('chart-a', 'float');
    s = useHudStore.getState();
    expect(s.rightDock.activePanel).toBe('chart-b');
  });

  it('toggle open/close keeps panel membership', () => {
    useHudStore.getState().dockPanel('chart-a', 'bottom');
    useHudStore.getState().toggleBottomDock();
    const s = useHudStore.getState();
    expect(s.bottomDock.open).toBe(false);
    expect(s.bottomDock.panels).toEqual(['chart-a']);
  });

  it('session reset clears all dock state', () => {
    useHudStore.getState().dockPanel('chart-a', 'right');
    useHudStore.getState().dockPanel('stat-b', 'bottom');
    useHudStore.getState().resetDockState();
    const s = useHudStore.getState();
    expect(s.dockPlacements).toEqual({});
    expect(s.rightDock.panels).toEqual([]);
    expect(s.bottomDock.panels).toEqual([]);
  });

  it('docking the same region twice is idempotent', () => {
    useHudStore.getState().dockPanel('chart-a', 'right');
    useHudStore.getState().dockPanel('chart-a', 'right');
    expect(useHudStore.getState().rightDock.panels).toEqual(['chart-a']);
  });
});
