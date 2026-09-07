/**
 * Wave 12（Visual Golden Corpus）：确定性布局几何断言语料。
 *
 * jsdom 无真实布局（getBoundingClientRect 全零），像素级 screenshot diff
 * 需要常驻服务（test/visual/capture.mjs 的 Playwright 基线通道）。本语料
 * 在 vitest 内确定性运行，把「几何正确性」压到三层可断言模型：
 *   1) 组件布局模型：全部目录类型经共享 resolver 落入合法槽位/堆叠，
 *      不出现 'none'（静默隐藏）—— table_panel 缺行类回归的通用防线；
 *   2) floating 像素契约：placement → 内联样式（left/top/width/z floor）；
 *   3) Layer Workspace 投影：100+ 图层（长名/分组/折叠/锁定）不丢行、
 *      分区有序、行结构类（truncate/行高）存在。
 * 涵盖 zh 长标题、tiny 面板（compact）与多组件 spec 的语料组合。
 */
import { describe, it, expect } from 'vitest';
import catalog from '@/lib/map-components/component-catalog.generated.json';
import { resolveMapComponents, DEFAULT_COMPONENT_ANCHOR } from '@/lib/map-components/resolve-components';
import {
  resolvePosition,
  positionClass,
  placementStyle,
  buildTopSlotIndexes,
  buildBottomSlotIndexes,
  stackedTopStyle,
  stackedBottomStyle,
  isFloating,
} from '@/components/map/map-components/helpers';
import type { MapSpecComponent } from '@/lib/mapspec-compiler/types';
import { projectWorkspace } from '@/lib/layers/workspace-projection';
import type { Layer } from '@/lib/types/layer';

/** 语料：全部 rendererRequired 目录类型各一实例（zh 长标题），双写 anchor。 */
function corpusSpec(): MapSpecComponent[] {
  const types = (catalog.componentTypes as Array<{ type: string; rendererRequired: boolean }>)
    .filter((t) => t.rendererRequired)
    .map((t) => t.type);
  return types.map((type, i) => ({
    id: `${type}-corpus`,
    type: type as MapSpecComponent['type'],
    enabled: true,
    position: 'top-left',
    options: {
      text: `非常长的中文标题语料 ${i} —— 「的专业制图工作台视觉语料库」（长度压力样本）`,
    },
  }));
}

describe('Wave 12 · 组件布局几何语料', () => {
  it('每个目录类型的组件都落入合法锚槽（无静默 none）', () => {
    const resolved = resolveMapComponents({ layout: { components: corpusSpec() } });
    expect(resolved.length).toBeGreaterThan(10);
    const hidden: string[] = [];
    for (const comp of resolved) {
      // basemap/export_layout/graticule/map_border 无槽位语义（'none' 合法
      // —— 全画布/叠加型）；其余必须有落点。
      const exempt = ['basemap', 'export_layout', 'graticule', 'map_border'].includes(comp.type);
      if (exempt) continue;
      const anchor = resolvePosition(comp);
      if (anchor === 'none' || !DEFAULT_COMPONENT_ANCHOR[comp.type]) {
        hidden.push(`${comp.type} → ${anchor}`);
      }
    }
    expect(hidden, `静默隐藏的组件类型: ${hidden.join(', ')}`).toEqual([]);
  });

  it('positionClass 只产生六槽或 hidden 词表', () => {
    const legal = new Set([
      'top-3 left-3', 'top-3 left-1/2 -translate-x-1/2', 'top-3 right-3',
      'bottom-3 left-3', 'bottom-3 left-1/2 -translate-x-1/2', 'bottom-3 right-3', 'hidden',
    ]);
    for (const comp of resolveMapComponents({ layout: { components: corpusSpec() } })) {
      expect(legal.has(positionClass(comp)), `${comp.type}: ${positionClass(comp)}`).toBe(true);
    }
  });

  it('同槽堆叠：索引连续且堆叠样式单调递增', () => {
    // 4 个 statistics_panel 挤同一槽（缺省 top-left）→ 求解器给 0..3。
    const many: MapSpecComponent[] = Array.from({ length: 4 }, (_, i) => ({
      id: `stats-${i}`,
      type: 'statistics_panel',
      enabled: true,
      options: { text: `统计面板 ${i}` },
    }));
    const resolved = resolveMapComponents({ layout: { components: many } });
    const topIndexes = buildTopSlotIndexes(resolved.map((c) => c.component));
    const idxList = [...topIndexes.values()].sort((a, b) => a - b);
    // 求解器给同槽成员发连续唯一索引（首个成员可无索引 = 基线位）。
    expect(new Set(idxList).size).toBe(idxList.length);
    expect(idxList[0]).toBe(0);
    const styles = resolved.map((c) => stackedTopStyle(c.component, topIndexes));
    // 拿到索引的成员有堆叠偏移；位移随索引不重复（同一时刻不重叠）。
    const tops = styles
      .map((s) => (s ? Number((s as { top: string }).top.match(/(\d+)px/)?.[1]) : null))
      .filter((v): v is number => v != null);
    expect(new Set(tops).size).toBe(tops.length);
  });

  it('底槽堆叠：scale_bar 缺省贴底、图例族上移不重叠', () => {
    const bottomPair: MapSpecComponent[] = [
      { id: 'bar', type: 'scale_bar', enabled: true },
      { id: 'leg', type: 'legend', enabled: true },
    ];
    const resolved = resolveMapComponents({ layout: { components: bottomPair } });
    const bottomIndexes = buildBottomSlotIndexes(resolved.map((c) => c.component));
    const barStyle = stackedBottomStyle(resolved[0].component, bottomIndexes);
    const legStyle = stackedBottomStyle(resolved[1].component, bottomIndexes);
    expect(String(barStyle?.bottom)).toContain('+ 30px');
    // legend 落 bottom-left 缺省 → 独立槽无堆叠 → 走基本偏移。
    expect(String(legStyle?.bottom)).toContain('+ 6px');
  });

  it('floating 像素契约：placement → 内联样式（取整 + z floor 40）', () => {
    const comp: MapSpecComponent = {
      id: 'chart-1',
      type: 'chart_panel',
      enabled: true,
      placement: { mode: 'floating', x: 12.6, y: 48.2, width: 320, height: 240, zIndex: 55 },
    };
    expect(isFloating(comp)).toBe(true);
    const style = placementStyle(comp);
    expect(style).toMatchObject({ left: 13, top: 48, width: 320, height: 240, zIndex: 55 });
    // 无 zIndex → floor 40（压锚定 chrome、不压弹层）。
    const floor = placementStyle({
      id: 'c2', type: 'chart_panel', enabled: true,
      placement: { mode: 'floating', x: 0, y: 0 },
    });
    expect(floor?.zIndex).toBe(40);
    // anchor 模式 → undefined（槽位类生效）。
    expect(placementStyle({ id: 't', type: 'title', enabled: true, position: 'top-center' })).toBeUndefined();
  });
});

describe('Wave 12 · Layer Workspace 投影语料', () => {
  function mkLayer(id: string, patch: Partial<Layer> = {}): Layer {
    return {
      id,
      name: id.length > 20 ? `${id}-成都城市群多尺度土地利用变化专题图（超长名称压力样本）` : id,
      type: 'vector', visible: true, opacity: 1, ...patch,
    } as Layer;
  }

  const layers: Layer[] = [
    ...Array.from({ length: 100 }, (_, i) => mkLayer(`layer-${String(i).padStart(3, '0')}`, { group: 'analysis' })),
    mkLayer('dem-90m', { type: 'raster', group: 'base' }),
    mkLayer('boundary-2024', { group: 'reference' }),
  ];
  const groups = [
    { id: 'g-long', name: '长名分组（视觉压力语料：超长分组名称会被 truncate 截断而不是换行撑高行）', collapsed: false },
    { id: 'g-folded', name: '折叠组', collapsed: true },
  ];
  const membership: Record<string, string> = {
    'layer-000': 'g-long', 'layer-001': 'g-long', 'layer-002': 'g-folded',
  };

  it('100+ 图层语料：不丢行、分区有序、折叠区不计可见行数', () => {
    const result = projectWorkspace({ layers, groups, membership, lockedLayerIds: ['dem-90m'], selectedLayerIds: ['layer-001'] });
    const total = result.sections.reduce((sum, s) => sum + s.rows.length, 0);
    expect(total).toBe(layers.length); // 102 行一个不少
    // 分区：用户组在前（创建序），语义区按词表序，default 恒最后。
    const names = result.sections.map((s) => s.name);
    expect(names.indexOf('长名分组（视觉压力语料：超长分组名称会被 truncate 截断而不是换行撑高行）'))
      .toBeLessThan(names.indexOf('base'));
    expect(names[names.length - 1]).toBe('analysis');
    // 折叠组行数不计入可见数；锁定/选中正确投影。
    const folded = result.sections.find((s) => s.id === 'g-folded');
    expect(folded?.rows).toHaveLength(1);
    expect(result.visibleRowCount).toBe(layers.length - 1);
    expect(result.sections.flatMap((s) => s.rows).find((r) => r.layer.id === 'dem-90m')?.locked).toBe(true);
    expect(result.sections.flatMap((s) => s.rows).find((r) => r.layer.id === 'layer-001')?.selected).toBe(true);
  });

  it('z-order 投影：分区内的行保持 store 数组序（拖拽重排后的可读性契约）', () => {
    const reordered = [...layers].reverse();
    const result = projectWorkspace({ layers: reordered, groups, membership });
    const analysisRows = result.sections.find((s) => s.name === 'analysis')!.rows.map((r) => r.layer.id);
    expect(analysisRows).toEqual([...analysisRows].sort().reverse()); // 逆序 store → 逆序分区行
  });
});
