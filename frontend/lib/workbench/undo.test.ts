/**
 * Undo/redo 命令模型（W4）行为测试。
 *
 * 锁定四条不变式：
 *   U1. recordCommand 前/后载荷已定 —— async 完成不回写历史，undo/redo
 *       是「反向重放」不是状态回写；
 *   U2. 有界历史（50），redo 分支在新命令入栈时清空；
 *   U3. clearUndoHistory 随会话切换（跨会话命令不可撤销）；
 *   U4. journal：record/journalOnly 写入 opsLog（含 reversible 元数据）。
 */
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { useHudStore } from '@/lib/store/useHudStore';
import { resetLiveState } from '@/lib/mapspec/session-cursor';

const commitPresentationSpy = vi.fn().mockResolvedValue(undefined);
const commitMutationSpy = vi.fn().mockResolvedValue({ mutation_revision: 1 });
vi.mock('@/lib/mapspec/user-mutation', () => ({
  commitLayerPresentation: (...args: unknown[]) => commitPresentationSpy(...args),
  commitMapSpecMutation: (...args: unknown[]) => commitMutationSpy(...args),
  reorderLayersAndCommit: vi.fn().mockResolvedValue(undefined),
}));
import {
  canRedo,
  canUndo,
  clearUndoHistory,
  docCommand,
  journalOnly,
  presentationCommand,
  recordCommand,
  redo,
  resetUndoForTests,
  UNDO_MAX_HISTORY,
  undo,
} from './undo';

describe('undo/redo 命令模型（W4）', () => {
  beforeEach(() => {
    resetUndoForTests();
    resetLiveState();
    useHudStore.setState({ layerGroups: [], layerGroupMembership: {}, lockedLayerIds: [], opsLog: [] });
  });

  it('U1: doc 命令 undo/redo 反向重放（组织态切片水合）', () => {
    const before = { version: 5 as const, groups: [], membership: {}, lockedLayerIds: [], mode: 'explore' as const };
    const after = {
      version: 5 as const,
      groups: [{ id: 'g1', name: '组', collapsed: false, parentId: null }],
      membership: { l1: 'g1' },
      lockedLayerIds: ['l2'],
      mode: 'explore' as const,
    };
    docCommand('新建分组', 'user', before, after);
    // 模拟正向变更已发生
    useHudStore.setState({
      layerGroups: [{ id: 'g1', name: '组', collapsed: false, parentId: null }],
      layerGroupMembership: { l1: 'g1' },
      lockedLayerIds: ['l2'],
    });
    expect(canUndo()).toBe(true);
    undo();
    expect(useHudStore.getState().layerGroups).toEqual([]);
    expect(useHudStore.getState().lockedLayerIds).toEqual([]);
    expect(canRedo()).toBe(true);
    redo();
    expect(useHudStore.getState().layerGroups).toHaveLength(1);
    expect(useHudStore.getState().lockedLayerIds).toEqual(['l2']);
  });

  it('U1b: presentation 命令的重放走 commitLayerPresentation 通道', async () => {
    // 预热动态 import（冷 worker 首解析可 >1s；并行跑下 waitFor(默认1s) 会超时）。
    await import('@/lib/mapspec/user-mutation');
    commitPresentationSpy.mockClear();
    presentationCommand('隐藏 L', 'L', 'user', { visible: true }, { visible: false });
    undo();
    redo();
    // 反向提交经动态 import —— 轮询等待落地（宽松超时防资源竞争抖动）。
    await vi.waitFor(
      () => {
        expect(commitPresentationSpy).toHaveBeenCalledTimes(2);
      },
      { timeout: 10_000 },
    );
    // undo 发起的是 before（visible:true），redo 是 after（visible:false）
    expect(commitPresentationSpy.mock.calls.map((c) => (c[0] as { visible?: boolean }).visible))
      .toEqual([true, false]);
  });

  it('U2: 有界历史与 redo 分支清空', () => {
    for (let i = 0; i < UNDO_MAX_HISTORY + 5; i++) {
      recordCommand({
        label: `cmd-${i}`,
        kind: 'toggle',
        actor: 'user',
        layerIds: [],
        undo: () => {},
        redo: () => {},
      });
    }
    expect(canUndo()).toBe(true);
    // 上限 50：多余命令把最老的挤出栈（首条不再可撤销）
    for (let i = 0; i < UNDO_MAX_HISTORY; i++) undo();
    expect(canUndo()).toBe(false);
    // 新命令入栈清 redo
    redo(); // no-op
    recordCommand({
      label: 'new',
      kind: 'toggle',
      actor: 'user',
      layerIds: [],
      undo: () => {},
      redo: () => {},
    });
    expect(canRedo()).toBe(false);
  });

  it('U3: clearUndoHistory 随会话切换清空两栈', () => {
    recordCommand({
      label: 'a',
      kind: 'toggle',
      actor: 'user',
      layerIds: [],
      undo: () => {},
      redo: () => {},
    });
    undo();
    expect(canRedo()).toBe(true);
    clearUndoHistory();
    expect(canUndo()).toBe(false);
    expect(canRedo()).toBe(false);
  });

  it('U4: journal 写入 opsLog 且可逆性元数据正确', () => {
    recordCommand({
      label: '隐藏 层A',
      kind: 'toggle',
      actor: 'agent',
      layerIds: ['层A'],
      undo: () => {},
      redo: () => {},
    });
    journalOnly({ type: 'remove', label: '删除图层 层B', actor: 'user' });
    const log = useHudStore.getState().opsLog;
    expect(log[0].type).toBe('remove');
    expect(log[0].reversible).toBe(false);
    expect(log[1].type).toBe('toggle');
    expect(log[1].reversible).toBe(true);
    expect(log[1].actor).toBe('agent');
  });

  it('R1-M1: reorder 重放走裸提交 —— 不再 recordCommand（undo 栈不被污染）', async () => {
    const { reorderCommand } = await import('./undo');
    const a = [{ id: 'l1' }, { id: 'l2' }];
    const b = [{ id: 'l2' }, { id: 'l1' }];
    reorderCommand('调整图层顺序', 'user', a, b);
    undo();
    redo();
    // 重放异步落地
    await new Promise((r) => setTimeout(r, 20));
    // redo 把命令放回 undo 栈（正常语义）—— 再 undo 一次后栈必须清空：
    // 不得存在重放产生的多余条目（bug 症状：重放经 reorderLayersAndCommit
    // → recordCommand 再入栈 → 一次 redo 需要 undo 两次才能抵消）。
    expect(undo()).toBe(true);
    expect(undo()).toBe(false);
    // 重放确实发起了裸提交
    expect(commitMutationSpy).toHaveBeenCalled();
  });

  it('空栈 undo/redo 返回 false（无异常）', () => {
    expect(undo()).toBe(false);
    expect(redo()).toBe(false);
  });
});
