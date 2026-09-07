/**
 * Workbench V4（Wave 2）：Layer Workspace UI 回归测试。
 * 锁定新能力契约：分组（创建/折叠/重命名入口）、锁定护栏（UI 禁操作）、
 * 批量操作条（显隐/删除确认）、搜索过滤、隔离入口、跨组投放换组。
 */
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';
import type { Layer } from '@/lib/types/layer';

const store: Record<string, any> = {
  layers: [] as Layer[],
  layerGroups: [] as Array<{ id: string; name: string; collapsed: boolean }>,
  layerGroupMembership: {} as Record<string, string>,
  lockedLayerIds: [] as string[],
  selectedLayerIds: [] as string[],
  isolatedLayerId: null as string | null,
  isolatedFrom: null,
  theme: 'dark',
  setActiveLeftTab: vi.fn(),
  setEditingLayerId: vi.fn(),
  focusLayer: vi.fn(),
  toggleLayer: vi.fn(),
  removeLayer: vi.fn(),
  updateLayer: vi.fn(),
  reorderLayers: vi.fn(),
  toggleLayerSelected: vi.fn((id: string) => {
    const cur: string[] = store.selectedLayerIds;
    store.selectedLayerIds = cur.includes(id) ? cur.filter((x) => x !== id) : [...cur, id];
  }),
  clearLayerSelection: vi.fn(() => {
    store.selectedLayerIds = [];
  }),
  toggleLayerLocked: vi.fn((id: string) => {
    const cur: string[] = store.lockedLayerIds;
    store.lockedLayerIds = cur.includes(id) ? cur.filter((x) => x !== id) : [...cur, id];
  }),
  createLayerGroup: vi.fn((name: string) => {
    const id = `wg-${store.layerGroups.length + 1}`;
    store.layerGroups = [...store.layerGroups, { id, name, collapsed: false }];
    return id;
  }),
  assignLayersToGroup: vi.fn((ids: string[], gid: string | null) => {
    const membership = { ...store.layerGroupMembership };
    for (const id of ids) {
      if (gid == null) delete membership[id];
      else membership[id] = gid;
    }
    store.layerGroupMembership = membership;
  }),
  toggleGroupCollapsed: vi.fn((gid: string) => {
    store.layerGroups = store.layerGroups.map((g: any) =>
      g.id === gid ? { ...g, collapsed: !g.collapsed } : g,
    );
  }),
  beginIsolate: vi.fn(),
  clearIsolate: vi.fn(),
};

const mutationMocks = vi.hoisted(() => ({
  toggleLayerAndCommit: vi.fn(),
  removeLayerAndCommit: vi.fn(),
  reorderLayersAndCommit: vi.fn(),
  setLayerOpacityAndCommit: vi.fn(),
}));

const opsMocks = vi.hoisted(() => ({
  isolateLayerAndCommit: vi.fn(),
  clearIsolateAndCommit: vi.fn(),
  batchSetVisibility: vi.fn(),
  batchSetOpacity: vi.fn(),
  pasteStyle: vi.fn(),
  retryLayerLoad: vi.fn(),
}));

vi.mock('@/lib/store/useHudStore', () => ({
  useHudStore: Object.assign(
    (selector: (s: any) => any) => selector(store),
    { getState: () => store },
  ),
}));
vi.mock('@/lib/mapspec/user-mutation', () => mutationMocks);
vi.mock('@/lib/layers/layer-ops', () => opsMocks);

import { LayersTab } from './layers-tab';

function makeLayer(overrides: Partial<Layer> = {}): Layer {
  return {
    id: 'L1',
    name: 'Layer One',
    type: 'vector',
    visible: true,
    opacity: 1,
    source: { type: 'FeatureCollection', features: [] },
    ...overrides,
  };
}

function setLayers(layers: Layer[]): void {
  store.layers = layers;
}

beforeEach(() => {
  vi.clearAllMocks();
  store.layers = [];
  store.layerGroups = [];
  store.layerGroupMembership = {};
  store.lockedLayerIds = [];
  store.selectedLayerIds = [];
  store.isolatedLayerId = null;
});

describe('Layer Workspace · 分组', () => {
  it('新建分组按钮创建用户组并渲染分组抬头', () => {
    setLayers([makeLayer()]);
    render(<LayersTab />);
    fireEvent.click(screen.getByRole('button', { name: '新建分组' }));
    expect(store.createLayerGroup).toHaveBeenCalledWith('分组 1');
  });

  it('组折叠隐藏成员行，但语义区行仍可见', () => {
    setLayers([makeLayer({ id: 'a' }), makeLayer({ id: 'b', name: 'B' })]);
    store.layerGroups = [{ id: 'g1', name: '东区', collapsed: true }];
    store.layerGroupMembership = { a: 'g1' };
    render(<LayersTab />);
    expect(screen.queryByTestId('layer-row-a')).toBeNull();
    expect(screen.getByTestId('layer-row-b')).toBeInTheDocument();
    const expand = screen.getByRole('button', { name: '展开分组 东区' });
    fireEvent.click(expand);
    expect(store.toggleGroupCollapsed).toHaveBeenCalledWith('g1');
  });

  it('组头「显示/隐藏分组全部图层」走批量通道', () => {
    setLayers([makeLayer({ id: 'a' })]);
    store.layerGroups = [{ id: 'g1', name: '东区', collapsed: false }];
    store.layerGroupMembership = { a: 'g1' };
    render(<LayersTab />);
    fireEvent.click(screen.getByRole('button', { name: '隐藏分组 东区 全部图层' }));
    expect(opsMocks.batchSetVisibility).toHaveBeenCalledWith(['a'], false);
  });

  it('选中的图层可通过组头按钮移入分组', () => {
    setLayers([makeLayer({ id: 'a' })]);
    store.layerGroups = [{ id: 'g1', name: '东区', collapsed: false }];
    store.selectedLayerIds = ['a'];
    render(<LayersTab />);
    fireEvent.click(screen.getByRole('button', { name: '将选中图层移入分组 东区' }));
    expect(store.assignLayersToGroup).toHaveBeenCalledWith(['a'], 'g1');
  });
});

describe('Layer Workspace · 锁定护栏', () => {
  it('锁定行禁用 显隐/删除/拖拽把手，锁定按钮可解锁', () => {
    setLayers([makeLayer({ id: 'a' })]);
    store.lockedLayerIds = ['a'];
    render(<LayersTab />);
    // 可见层的眼睛按钮语义是「隐藏图层」；锁定时应禁用。
    expect(screen.getByRole('button', { name: '隐藏图层' })).toBeDisabled();
    expect(screen.getByRole('button', { name: '编辑图层样式 Layer One' })).toBeDisabled();
    expect(screen.getByRole('button', { name: '删除图层' })).toBeDisabled();
    expect(screen.getByTestId('layer-row-a').getAttribute('data-locked')).toBe('true');
    fireEvent.click(screen.getByRole('button', { name: '解锁图层 Layer One' }));
    expect(store.toggleLayerLocked).toHaveBeenCalledWith('a');
  });

  it('未锁定行锁定按钮写入 lock 状态', () => {
    setLayers([makeLayer({ id: 'a' })]);
    render(<LayersTab />);
    fireEvent.click(screen.getByRole('button', { name: '锁定图层 Layer One' }));
    expect(store.toggleLayerLocked).toHaveBeenCalledWith('a');
  });
});

describe('Layer Workspace · 批量操作', () => {
  it('选择 checkbox 写入选择状态（无重渲染断言 store 变更）', () => {
    setLayers([makeLayer({ id: 'a' })]);
    render(<LayersTab />);
    fireEvent.click(screen.getByRole('button', { name: '选择 Layer One' }));
    expect(store.toggleLayerSelected).toHaveBeenCalledWith('a');
  });

  it('预置选择出现批量条；全部隐藏走批量通道；取消选择清空', () => {
    setLayers([makeLayer({ id: 'a' }), makeLayer({ id: 'b', name: 'B' })]);
    store.selectedLayerIds = ['a'];
    render(<LayersTab />);
    expect(screen.getByTestId('layer-batch-bar')).toBeInTheDocument();
    expect(screen.getByText('已选 1')).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: '全部隐藏' }));
    expect(opsMocks.batchSetVisibility).toHaveBeenCalledWith(['a'], false);
    fireEvent.click(screen.getByRole('button', { name: '取消选择' }));
    expect(store.selectedLayerIds).toHaveLength(0);
  });

  it('批量删除走两段确认且只删未锁层', () => {
    setLayers([makeLayer({ id: 'a' }), makeLayer({ id: 'b', name: 'B' })]);
    store.selectedLayerIds = ['a', 'b'];
    store.lockedLayerIds = ['b'];
    render(<LayersTab />);
    fireEvent.click(screen.getByRole('button', { name: /删除$/ }));
    fireEvent.click(screen.getByRole('button', { name: '确认' }));
    expect(mutationMocks.removeLayerAndCommit).toHaveBeenCalledTimes(1);
    expect(mutationMocks.removeLayerAndCommit).toHaveBeenCalledWith('a');
  });
});

describe('Layer Workspace · 搜索', () => {
  it('按名称过滤行', () => {
    setLayers([makeLayer({ id: 'a', name: 'POI 结果' }), makeLayer({ id: 'b', name: '区县边界' })]);
    render(<LayersTab />);
    fireEvent.change(screen.getByRole('searchbox', { name: /搜索图层/ }), { target: { value: 'poi' } });
    expect(screen.getByTestId('layer-row-a')).toBeInTheDocument();
    expect(screen.queryByTestId('layer-row-b')).toBeNull();
  });
});

describe('Layer Workspace · 隔离与重试', () => {
  it('更多操作展开后可进入隔离；隔离态行展示「隔离」徽标与退出入口', () => {
    setLayers([makeLayer({ id: 'a' })]);
    store.isolatedLayerId = 'a';
    render(<LayersTab />);
    expect(screen.getByText('隔离')).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: '退出隔离' }));
    expect(opsMocks.clearIsolateAndCommit).toHaveBeenCalled();
  });

  it('更多操作 → 隔离按钮调用 isolateLayerAndCommit', () => {
    setLayers([makeLayer({ id: 'a' })]);
    render(<LayersTab />);
    fireEvent.click(screen.getByRole('button', { name: '更多操作 Layer One' }));
    fireEvent.click(screen.getByRole('button', { name: '隔离显示 Layer One（其余图层隐藏）' }));
    expect(opsMocks.isolateLayerAndCommit).toHaveBeenCalledWith('a');
  });

  it('ref 承载层展开更多后提供重载入口', () => {
    setLayers([makeLayer({ id: 'a', _refId: 'ref:geojson/x' })]);
    render(<LayersTab />);
    fireEvent.click(screen.getByRole('button', { name: '更多操作 Layer One' }));
    fireEvent.click(screen.getByRole('button', { name: '重新加载数据 Layer One' }));
    expect(opsMocks.retryLayerLoad).toHaveBeenCalledWith('a');
  });
});

describe('Layer Workspace · 拖拽换组', () => {
  it('跨组行投放 = 重排 + 换组（B4 修复的 UI 面）', () => {
    setLayers([makeLayer({ id: 'a' }), makeLayer({ id: 'b', name: 'B' })]);
    store.layerGroups = [{ id: 'g1', name: '东区', collapsed: false }];
    store.layerGroupMembership = { b: 'g1' };
    render(<LayersTab />);
    const rowA = screen.getByTestId('layer-row-a');
    fireEvent.dragStart(rowA);
    const rowB = screen.getByTestId('layer-row-b');
    fireEvent.dragOver(rowB);
    fireEvent.drop(rowB);
    // a 投到 b（b ∈ g1）→ a 换入 g1；重排走既有通道
    expect(store.assignLayersToGroup).toHaveBeenCalledWith(['a'], 'g1');
    expect(mutationMocks.reorderLayersAndCommit).toHaveBeenCalledTimes(1);
  });
});
