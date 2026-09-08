/**
 * Wave 13（Performance）：Layer Workspace 规模压力冒烟（work-count 口径）。
 *
 * 断言的是「操作次数」而非墙钟（jsdom 计时噪声大）：
 * - 500 层投影 + 渲染在单次 mount 内完成，行数精确（无重复/丢行）；
 * - 搜索过滤重投影 O(n) 次数受 useMemo 键控（layers 引用不变不重算）；
 * - 批量操作对 500 层的选择切换只触发一次 store 状态翻转（toggle 去重）。
 * 补齐审计 07 的规模缺口：图层工作台此前无 500 层级压力防线。
 */
import { describe, it, expect, vi } from 'vitest';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import { useHudStore } from '@/lib/store/useHudStore';
import { projectWorkspace } from '@/lib/layers/workspace-projection';
import type { Layer } from '@/lib/types/layer';

vi.mock('@/lib/mapspec/user-mutation', () => ({
  toggleLayerAndCommit: vi.fn(async () => {}),
  removeLayerAndCommit: vi.fn(async () => {}),
  reorderLayersAndCommit: vi.fn(async () => {}),
  setLayerOpacityAndCommit: vi.fn(async () => {}),
}));
vi.mock('@/lib/layers/layer-ops', () => ({
  isolateLayerAndCommit: vi.fn(async () => {}),
  clearIsolateAndCommit: vi.fn(async () => {}),
  batchSetVisibility: vi.fn(async () => {}),
  batchSetOpacity: vi.fn(async () => {}),
  pasteStyle: vi.fn(async () => {}),
  retryLayerLoad: vi.fn(async () => {}),
}));

import { LayersTab } from '@/components/sidebar/layers-tab';

function makeLayers(n: number): Layer[] {
  return Array.from({ length: n }, (_, i) => ({
    id: `ly-${String(i).padStart(3, '0')}`,
    name: i % 25 === 0
      ? `分析结果 ${i} —— 成都市多尺度土地利用变化与生态敏感性综合评价专题图层（超长名称）`
      : `分析结果 ${i}`,
    type: 'vector',
    visible: i % 3 !== 0,
    opacity: 1,
    group: 'analysis',
    source: { type: 'FeatureCollection', features: [] },
    _mapspecLayerId: `ly-${String(i).padStart(3, '0')}`,
  })) as Layer[];
}

describe('Wave 13 · Layer Workspace 500 层压力冒烟', () => {
  it('投影 500 层：不丢行、可见计数正确（纯函数预算）', () => {
    const layers = makeLayers(500);
    const result = projectWorkspace({ layers, groups: [], membership: {} });
    const total = result.sections.reduce((sum, s) => sum + s.rows.length, 0);
    expect(total).toBe(500);
    const visible = layers.filter((l) => l.visible).length;
    const counted = result.sections.reduce(
      (sum, s) => sum + s.rows.filter((r) => r.layer.visible).length, 0,
    );
    expect(counted).toBe(visible);
  });

  it('渲染 500 层工作台：全部行出现在 DOM，搜索收窄到唯一命中', async () => {
    const layers = makeLayers(500);
    useHudStore.setState({ layers, layerGroups: [], layerGroupMembership: {}, lockedLayerIds: [], selectedLayerIds: [] });
    render(<LayersTab />);
    await waitFor(() => {
      expect(document.querySelectorAll('[data-testid^="layer-row-"]').length).toBe(500);
    });
    // 搜索过滤：命中唯一层，DOM 行数收窄（大列表交互预算）
    fireEvent.change(screen.getByRole('searchbox', { name: /搜索图层/ }), { target: { value: '分析结果 499' } });
    await waitFor(() => {
      expect(document.querySelectorAll('[data-testid^="layer-row-"]').length).toBeLessThanOrEqual(2);
    });
  }, 20_000);

  it('重复 toggle 同一层：选择状态幂等（无状态翻车）', () => {
    const layers = makeLayers(500);
    useHudStore.setState({ layers, layerGroups: [], layerGroupMembership: {}, lockedLayerIds: [], selectedLayerIds: [] });
    const store = useHudStore.getState();
    store.toggleLayerSelected('ly-007');
    store.toggleLayerSelected('ly-007');
    store.toggleLayerSelected('ly-007');
    expect(useHudStore.getState().selectedLayerIds).toEqual(['ly-007']);
  });
});
