/**
 * Workbench V4（Wave 2）：Layer Workspace 纯投影层契约测试。
 * 锁定：分组树视图、z-order 保持、搜索过滤、lock/selected 投影。
 */
import { describe, it, expect } from 'vitest';
import { projectWorkspace, semanticGroupLabel } from './workspace-projection';
import type { Layer } from '@/lib/types/layer';

function mkLayer(id: string, patch: Partial<Layer> = {}): Layer {
  return { id, name: `层-${id}`, type: 'vector', visible: true, opacity: 1, ...patch } as Layer;
}

const baseInput = {
  layers: [] as Layer[],
  groups: [],
  membership: {},
};

describe('projectWorkspace · 用户分组', () => {
  it('组按创建序展示；成员保持 store 数组序（z-order 可读性）', () => {
    const layers = [mkLayer('a'), mkLayer('b'), mkLayer('c'), mkLayer('d')];
    const groups = [
      { id: 'g1', name: '东部', collapsed: false },
      { id: 'g2', name: '西部', collapsed: false },
    ];
    const result = projectWorkspace({
      ...baseInput,
      layers,
      groups,
      membership: { a: 'g2', c: 'g1', d: 'g1' },
    });
    // 未按用户分组的 b 落入 default 语义区（恒最后）。
    expect(result.sections.map((s) => s.name)).toEqual(['东部', '西部', 'default']);
    // g1 成员按 store 序 c, d；g2 成员 a
    expect(result.sections[0].rows.map((r) => r.layer.id)).toEqual(['c', 'd']);
    expect(result.sections[1].rows.map((r) => r.layer.id)).toEqual(['a']);
    expect(result.visibleRowCount).toBe(4);
  });

  it('未分组行落入语义区；default 恒最后；词表外语义组不丢行', () => {
    const layers = [
      mkLayer('a', { group: 'analysis' }),
      mkLayer('b', { group: 'base' }),
      mkLayer('c', { group: 'default' }),
      mkLayer('d', { group: 'weird-custom' as any }),
    ];
    const result = projectWorkspace({ ...baseInput, layers });
    expect(result.sections.map((s) => s.name)).toEqual(['base', 'analysis', 'default', 'weird-custom']);
    expect(result.sections.map((s) => s.rows.length)).toEqual([1, 1, 1, 1]);
  });

  it('membership 指向已删除的组 → 行回落语义区（组实体离场即失效）', () => {
    const layers = [mkLayer('a', { group: 'analysis' })];
    const groups: { id: string; name: string; collapsed: boolean }[] = [];
    const result = projectWorkspace({
      layers,
      groups,
      membership: { a: 'wg-gone' },
    });
    expect(result.sections).toHaveLength(1);
    expect(result.sections[0].name).toBe('analysis');
    expect(result.sections[0].rows).toHaveLength(1);
  });

  it('折叠区行不计入 visibleRowCount，但行仍投影（折叠 ≠ 删除）', () => {
    const layers = [mkLayer('a'), mkLayer('b')];
    const groups = [{ id: 'g1', name: 'g', collapsed: true }];
    const result = projectWorkspace({
      ...baseInput,
      layers,
      groups,
      membership: { a: 'g1' },
    });
    expect(result.sections[0].collapsed).toBe(true);
    expect(result.sections[0].rows).toHaveLength(1);
    expect(result.visibleRowCount).toBe(1); // 只有未分组的 b
  });
});

describe('projectWorkspace · search / lock / selected', () => {
  const layers = [mkLayer('a', { name: 'POI 查询结果' }), mkLayer('b', { name: '区县专题图' })];

  it('搜索按名称大小写不敏感子串过滤', () => {
    const result = projectWorkspace({ ...baseInput, layers, search: 'poi' });
    expect(result.sections.flatMap((s) => s.rows).map((r) => r.layer.id)).toEqual(['a']);
  });

  it('搜索按 id 与 refId 匹配', () => {
    const withRef = [mkLayer('a', { _refId: 'ref:raster/dem-90m' })];
    expect(
      projectWorkspace({ ...baseInput, layers: withRef, search: 'dem-90' })
        .sections.flatMap((s) => s.rows),
    ).toHaveLength(1);
    expect(
      projectWorkspace({ ...baseInput, layers, search: '  ' })
        .visibleRowCount,
    ).toBe(2);
  });

  it('locked / selected 投影到行', () => {
    const result = projectWorkspace({
      ...baseInput,
      layers,
      lockedLayerIds: ['a'],
      selectedLayerIds: ['a', 'b'],
    });
    const rows = result.sections.flatMap((s) => s.rows);
    expect(rows.find((r) => r.layer.id === 'a')?.locked).toBe(true);
    expect(rows.find((r) => r.layer.id === 'a')?.selected).toBe(true);
    expect(rows.find((r) => r.layer.id === 'b')?.locked).toBe(false);
  });
});

describe('semanticGroupLabel', () => {
  it('封闭词表', () => {
    expect(semanticGroupLabel('analysis')).toBe('分析结果');
    expect(semanticGroupLabel('base')).toBe('底图');
    expect(semanticGroupLabel('reference')).toBe('参考数据');
    expect(semanticGroupLabel('default')).toBe('未分组');
    expect(semanticGroupLabel('custom')).toBe('custom');
  });
});
