/**
 * Wave 8：Layer Workspace 的对比入口（more 行 ComparePicker）。
 * 契约：候选排除本层族与隐藏层；选中后以 spec 层族 id 进入 workbenchSlice
 * 的 comparison（UI projection —— 不经 user-mutation，不写 MapSpec）。
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
  comparison: {
    active: false,
    kind: 'swipe',
    primaryLayerId: null,
    secondaryLayerId: null,
    syncPan: true,
    syncZoom: true,
    position: 0.5,
  },
  setActiveLeftTab: vi.fn(),
  setEditingLayerId: vi.fn(),
  focusLayer: vi.fn(),
  toggleLayerSelected: vi.fn(),
  clearLayerSelection: vi.fn(),
  toggleLayerLocked: vi.fn(),
  createLayerGroup: vi.fn(() => 'wg-1'),
  assignLayersToGroup: vi.fn(),
  toggleGroupCollapsed: vi.fn(),
  pruneLayerGroups: vi.fn(),
  beginIsolate: vi.fn(),
  clearIsolate: vi.fn(),
  enterComparison: vi.fn((patch: Record<string, unknown> = {}) => {
    store.comparison = { ...store.comparison, ...patch, active: true };
  }),
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
    source: {
      type: 'FeatureCollection',
      features: [],
    },
    ...overrides,
  } as Layer;
}

beforeEach(() => {
  vi.clearAllMocks();
  store.layers = [
    makeLayer({ id: 'F1', name: '人口密度', _mapspecLayerId: 'spec-a' } as Partial<Layer> as Layer),
    makeLayer({ id: 'F2', name: '路网', _mapspecLayerId: 'spec-b' } as Partial<Layer> as Layer),
    makeLayer({ id: 'F3', name: '隐藏层', visible: false, _mapspecLayerId: 'spec-c' } as Partial<Layer> as Layer),
  ];
  store.comparison = {
    active: false,
    kind: 'swipe',
    primaryLayerId: null,
    secondaryLayerId: null,
    syncPan: true,
    syncZoom: true,
    position: 0.5,
  };
});

describe('LayersTab · ComparePicker（Wave 8 对比入口）', () => {
  it('more 行内渲染对比选择器，候选排除本层族与隐藏层', () => {
    render(<LayersTab />);
    fireEvent.click(screen.getByLabelText('更多操作 人口密度'));
    const moreRow = screen.getByTestId('layer-more-F1');
    expect(moreRow).toBeInTheDocument();

    const picker = screen.getByLabelText('对比显示 人口密度');
    expect(picker).toBeInTheDocument();
    const options = Array.from((picker as HTMLSelectElement).options).map((o) => o.value);
    // 占位空值 + 只有路网（本层族 spec-a 与隐藏层 spec-c 都不入列）
    expect(options).toEqual(['', 'spec-b']);
  });

  it('选中候选 → enterComparison({primaryLayerId, secondaryLayerId})（族 id 契约）', () => {
    render(<LayersTab />);
    fireEvent.click(screen.getByLabelText('更多操作 人口密度'));
    fireEvent.change(screen.getByLabelText('对比显示 人口密度'), { target: { value: 'spec-b' } });
    expect(store.enterComparison).toHaveBeenCalledWith({
      primaryLayerId: 'spec-a',
      secondaryLayerId: 'spec-b',
    });
    expect(store.comparison.active).toBe(true);
  });

  it('无其他可见层时选择器禁用（诚实 UI，不提供空操作入口）', () => {
    store.layers = [makeLayer({ id: 'F1', name: '人口密度', _mapspecLayerId: 'spec-a' } as Partial<Layer> as Layer)];
    render(<LayersTab />);
    fireEvent.click(screen.getByLabelText('更多操作 人口密度'));
    expect(screen.getByLabelText('对比显示 人口密度')).toBeDisabled();
  });
});
