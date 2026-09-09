/**
 * WorkbenchDoc V5（W1）模型测试：V4 迁移、恢复归一化、reparent 守卫、
 * 树投影序。全部纯函数 —— 无 store / 网络依赖。
 */
import { describe, expect, it } from 'vitest';
import {
  WORKBENCH_GROUP_MAX_DEPTH,
  canReparentGroup,
  descendantGroupIds,
  emptyWorkbenchDoc,
  groupDepth,
  migrateV4Groups,
  normalizeWorkbenchDoc,
  rootGroupsFirst,
  workbenchDocBytes,
} from './doc';

const g = (id: string, parentId: string | null = null, collapsed = false) => ({
  id,
  name: id,
  collapsed,
  parentId,
});

describe('migrateV4Groups', () => {
  it('V4 扁平实体全部升级为根组，成员/锁/模式原样保留', () => {
    const doc = migrateV4Groups(
      [
        { id: 'wg-1', name: '兴趣区', collapsed: true },
        { id: 'wg-2', name: '底图组', collapsed: false },
      ],
      { 'layer-a': 'wg-1' },
      ['layer-b'],
      'analyze',
    );
    expect(doc.version).toBe(5);
    expect(doc.groups).toEqual([
      { id: 'wg-1', name: '兴趣区', collapsed: true, parentId: null },
      { id: 'wg-2', name: '底图组', collapsed: false, parentId: null },
    ]);
    expect(doc.membership).toEqual({ 'layer-a': 'wg-1' });
    expect(doc.lockedLayerIds).toEqual(['layer-b']);
    expect(doc.mode).toBe('analyze');
  });

  it('空 V4 状态 → 空 V5 doc（与 emptyWorkbenchDoc 等价）', () => {
    const doc = migrateV4Groups([], {}, [], 'explore');
    expect(doc).toEqual(emptyWorkbenchDoc());
  });
});

describe('normalizeWorkbenchDoc', () => {
  it('合法嵌套 doc 原样恢复', () => {
    const raw = {
      version: 5,
      groups: [g('a'), g('b', 'a')],
      membership: { 'l1': 'b' },
      lockedLayerIds: ['l2'],
      mode: 'compose',
    };
    const doc = normalizeWorkbenchDoc(raw);
    expect(doc).toEqual(raw);
  });

  it('拒绝非 V5 / 缺 groups 的 payload', () => {
    expect(normalizeWorkbenchDoc(null)).toBeNull();
    expect(normalizeWorkbenchDoc({ version: 4, groups: [] })).toBeNull();
    expect(normalizeWorkbenchDoc({ version: 5 })).toBeNull();
  });

  it('孤儿组提升为根；重复 id 与无名组被修复', () => {
    const doc = normalizeWorkbenchDoc({
      version: 5,
      groups: [g('a'), g('b', 'ghost'), { id: 'a', name: '', parentId: null }],
      membership: {},
      lockedLayerIds: [],
      mode: 'explore',
    });
    expect(doc).not.toBeNull();
    const b = doc!.groups.find((x) => x.id === 'b');
    expect(b?.parentId).toBeNull(); // 父 ghost 不存在 → 根
    expect(doc!.groups.filter((x) => x.id === 'a')).toHaveLength(1);
    expect(doc!.groups.find((x) => x.id === 'a')?.name).toBe('a');
  });

  it('恢复 payload 中的环被截断（b→a→b 不产生死循环）', () => {
    const doc = normalizeWorkbenchDoc({
      version: 5,
      groups: [g('a', 'b'), g('b', 'a')],
      membership: {},
      lockedLayerIds: [],
      mode: 'explore',
    });
    expect(doc).not.toBeNull();
    for (const node of doc!.groups) {
      expect(groupDepth(doc!.groups, node.id)).toBeLessThanOrEqual(WORKBENCH_GROUP_MAX_DEPTH);
    }
  });

  it('成员/锁过滤非字符串键值', () => {
    const doc = normalizeWorkbenchDoc({
      version: 5,
      groups: [],
      membership: { ok: 'a', bad: 42 } as unknown as Record<string, string>,
      lockedLayerIds: ['ok', 7] as unknown as string[],
      mode: 'explore',
    });
    expect(doc!.membership).toEqual({ ok: 'a' });
    expect(doc!.lockedLayerIds).toEqual(['ok']);
  });
});

describe('reparent / depth 守卫', () => {
  const tree = [g('root'), g('mid', 'root'), g('leaf', 'mid')];

  it('合法挂载：根下新子组', () => {
    expect(canReparentGroup(tree, 'leaf', 'root')).toBe(true);
    expect(canReparentGroup(tree, 'leaf', null)).toBe(true);
  });

  it('拒绝挂到自己 / 自己的子孙（成环）', () => {
    expect(canReparentGroup(tree, 'mid', 'mid')).toBe(false);
    expect(canReparentGroup(tree, 'root', 'leaf')).toBe(false);
  });

  it('拒绝指向不存在的父', () => {
    expect(canReparentGroup(tree, 'leaf', 'ghost')).toBe(false);
  });

  it('深度上限：满深子树不能再下挂', () => {
    // 构造满深链 d1<d2<d3<d4（上限 4），把 d1 挂到新根下会超限。
    const deep = [
      g('n1'),
      g('n2', 'n1'),
      g('n3', 'n2'),
      g('n4', 'n3'),
      g('n5', 'n4'),
    ];
    expect(groupDepth(deep, 'n5')).toBe(5);
    // n1 挂到 n5 下 → 深度 6 > 上限 → 拒绝（n5 是 n1 的子孙也被环守卫拒绝）
    expect(canReparentGroup(deep, 'n1', 'n5')).toBe(false);
    // 合法方向：把 n5 整体提为根
    expect(canReparentGroup(deep, 'n5', null)).toBe(true);
  });
});

describe('树遍历辅助', () => {
  const tree = [g('r1'), g('c1', 'r1'), g('c2', 'r1'), g('gc', 'c1'), g('r2')];

  it('descendantGroupIds 不含自身且覆盖全后代', () => {
    expect(descendantGroupIds(tree, 'r1')).toEqual(new Set(['c1', 'c2', 'gc']));
    expect(descendantGroupIds(tree, 'gc')).toEqual(new Set());
  });

  it('rootGroupsFirst 保持创建序且忽略父子', () => {
    expect(rootGroupsFirst(tree).map((x) => x.id)).toEqual(['r1', 'r2']);
  });
});

describe('workbenchDocBytes', () => {
  it('与 JSON 字节数一致且远小于 64KB 上限（空 doc）', () => {
    const bytes = workbenchDocBytes(emptyWorkbenchDoc());
    expect(bytes).toBe(JSON.stringify(emptyWorkbenchDoc()).length);
    expect(bytes).toBeLessThan(1024);
  });
});
