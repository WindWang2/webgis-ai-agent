/**
 * Workbench delta 纯函数（V6）—— 与后端 collab/delta.py 语义镜像的行为测试。
 * 锁定：绝对值语义（重放幂等）、部分字段组更新、级联删除、引用检查、
 * field 级 inverse（同节点异字段并发不互相覆盖 —— §15 必测）。
 */
import { describe, expect, it } from 'vitest';
import {
  applyWorkbenchDelta,
  diffWorkbenchDocs,
  invertWorkbenchDelta,
  WorkbenchDeltaError,
  type WorkbenchDelta,
} from './delta';
import { emptyWorkbenchDoc, type WorkbenchDocV5 } from './doc';

function docA(): WorkbenchDocV5 {
  return {
    version: 5,
    groups: [
      { id: 'g1', name: 'A', collapsed: false, parentId: null },
      { id: 'g2', name: 'B', collapsed: false, parentId: 'g1' },
    ],
    membership: { l1: 'g2' },
    lockedLayerIds: ['l1'],
    mode: 'explore',
  };
}

describe('applyWorkbenchDelta', () => {
  it('部分字段更新：只动出现的键', () => {
    const out = applyWorkbenchDelta(docA(), { setGroups: [{ id: 'g2', collapsed: true }] });
    const g2 = out.groups.find((g) => g.id === 'g2')!;
    expect(g2.collapsed).toBe(true);
    expect(g2.name).toBe('B'); // 未触碰字段保持
  });

  it('创建组要求 name；级联删除清 membership', () => {
    expect(() => applyWorkbenchDoc(docA(), { setGroups: [{ id: 'g9' }] })).toThrow(WorkbenchDeltaError);
    const out = applyWorkbenchDelta(docA(), { removeGroupIds: ['g1'] });
    expect(out.groups).toHaveLength(0);
    expect(out.membership).toEqual({});
  });

  it('membershipSet 目标必须存在；Set 优先于 Clear', () => {
    expect(() =>
      applyWorkbenchDelta(docA(), { membershipSet: [{ layerId: 'l9', groupId: 'gX' }] }),
    ).toThrow(WorkbenchDeltaError);
    const out = applyWorkbenchDelta(docA(), {
      membershipSet: [{ layerId: 'l1', groupId: 'g1' }],
      membershipClear: ['l1'],
    });
    expect(out.membership).toEqual({ l1: 'g1' });
  });

  it('绝对值语义：重放幂等（revision 门控采纳的安全前提）', () => {
    const d: WorkbenchDelta = {
      setGroups: [{ id: 'g2', collapsed: true }],
      membershipSet: [{ layerId: 'l1', groupId: 'g1' }],
      locksAdd: ['l2'],
    };
    const once = applyWorkbenchDelta(docA(), d);
    const twice = applyWorkbenchDelta(once, d);
    expect(twice).toEqual(once);
  });
});

// 辅助：applyWorkbenchDelta 的直接引用（避免测试内重复类型断言）。
function applyWorkbenchDoc(doc: WorkbenchDocV5, delta: WorkbenchDelta): WorkbenchDocV5 {
  return applyWorkbenchDelta(doc, delta);
}

describe('diffWorkbenchDocs', () => {
  it('最小 delta：改名只回 name；无差异返回 null', () => {
    const before = docA();
    const after: WorkbenchDocV5 = {
      ...before,
      groups: before.groups.map((g) => (g.id === 'g1' ? { ...g, name: 'A2' } : g)),
    };
    const delta = diffWorkbenchDocs(before, after)!;
    expect(delta.setGroups).toEqual([{ id: 'g1', name: 'A2' }]);
    expect(diffWorkbenchDocs(before, before)).toBeNull();
  });

  it('mode-only 差异返回 null（mode 不在 delta 域，走全量回退）', () => {
    const delta = diffWorkbenchDocs(docA(), { ...docA(), mode: 'compose' });
    expect(delta).toBeNull();
  });

  it('apply∘diff = identity（round-trip）', () => {
    const before = docA();
    const after: WorkbenchDocV5 = {
      version: 5,
      groups: [
        { id: 'g1', name: 'A', collapsed: true, parentId: null },
        { id: 'g3', name: 'C', collapsed: false, parentId: 'g1' },
      ],
      membership: { l2: 'g3' },
      lockedLayerIds: [],
      mode: 'explore',
    };
    const delta = diffWorkbenchDocs(before, after)!;
    expect(applyWorkbenchDelta(before, delta)).toEqual(after);
  });
});

describe('invertWorkbenchDelta（并发安全 undo）', () => {
  it('同节点异字段并发：undo 只回本命令字段（§15 必测）', () => {
    // A 改名 g1（forward）。并发：B 在 A 提交后把 g1 折叠（不在 A 的 undo 域）。
    const before = docA();
    const forward = diffWorkbenchDocs(
      before,
      { ...before, groups: before.groups.map((g) => (g.id === 'g1' ? { ...g, name: 'A2' } : g)) },
    )!;
    const inverse = invertWorkbenchDelta(forward, before);
    // B 折叠后的现实态（g1 collapsed=true）：
    const reality: WorkbenchDocV5 = {
      ...before,
      groups: before.groups.map((g) => (g.id === 'g1' ? { ...g, name: 'A2', collapsed: true } : g)),
    };
    const undone = applyWorkbenchDelta(reality, inverse);
    const g1 = undone.groups.find((g) => g.id === 'g1')!;
    expect(g1.name).toBe('A'); // A 的改名被撤销
    expect(g1.collapsed).toBe(true); // B 的折叠存活（no whole-table rollback）
  });

  it('删除组的 inverse 恢复整个子树 + membership', () => {
    const before = docA();
    const forward = diffWorkbenchDocs(before, emptyWorkbenchDoc())!;
    const inverse = invertWorkbenchDelta(forward, before);
    const undone = applyWorkbenchDelta(emptyWorkbenchDoc(), inverse);
    expect(undone.groups.map((g) => g.id).sort()).toEqual(['g1', 'g2']);
    expect(undone.membership).toEqual({ l1: 'g2' });
  });

  it('创建组的 inverse = 删除', () => {
    const before = emptyWorkbenchDoc();
    const created: WorkbenchDocV5 = {
      ...before,
      groups: [{ id: 'n1', name: '新', collapsed: false, parentId: null }],
      membership: { lx: 'n1' },
    };
    const forward = diffWorkbenchDocs(before, created)!;
    const inverse = invertWorkbenchDelta(forward, before);
    const undone = applyWorkbenchDelta(created, inverse);
    expect(undone.groups).toHaveLength(0);
    expect(undone.membership).toEqual({});
  });

  it('locks 反演是精确的集合逆', () => {
    const before = docA();
    const forward: WorkbenchDelta = { locksAdd: ['l2'], locksRemove: ['l1'] };
    const inverse = invertWorkbenchDelta(forward, before);
    const undone = applyWorkbenchDelta(applyWorkbenchDelta(before, forward), inverse);
    expect(undone.lockedLayerIds.sort()).toEqual(before.lockedLayerIds.sort());
  });
});
