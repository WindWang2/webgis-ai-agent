/**
 * Editing UX / artifact linkage / a11y 批次（W9/W10/W12）行为测试。
 *
 *   E1. W9：嵌套子组创建（组头按钮 → createLayerGroup(name, parentId)）
 *       与 undo 接线（锁定/换组可撤销）；
 *   E2. W10：provenance 徽标 —— 有 provenance.result_ref 的行渲染徽标，
 *       点击切换到 results tab；无 provenance 的行不渲染；
 *   E3. W12：Escape 退出对比覆盖层（键盘路径）。
 */
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';
import { useHudStore } from '@/lib/store/useHudStore';
import { resetLiveState } from '@/lib/mapspec/session-cursor';
import { LayersTab } from '@/components/sidebar/layers-tab';
import { useWorkbenchUndoKeys } from '@/lib/workbench/use-undo';
import type { Layer } from '@/lib/types/layer';

vi.mock('@/lib/mapspec/user-mutation', () => ({
  toggleLayerAndCommit: vi.fn(async () => {}),
  removeLayerAndCommit: vi.fn(async () => {}),
  reorderLayersAndCommit: vi.fn(async () => {}),
  setLayerOpacityAndCommit: vi.fn(async () => {}),
  commitLayerPresentation: vi.fn(async () => {}),
  commitMapSpecMutation: vi.fn(async () => ({ mutation_revision: 1 })),
}));
vi.mock('@/lib/layers/layer-ops', () => ({
  isolateLayerAndCommit: vi.fn(async () => {}),
  clearIsolateAndCommit: vi.fn(async () => {}),
  batchSetVisibility: vi.fn(async () => {}),
  batchSetOpacity: vi.fn(async () => {}),
  pasteStyle: vi.fn(async () => {}),
  retryLayerLoad: vi.fn(async () => {}),
}));

function makeLayer(id: string, patch: Partial<Layer> = {}): Layer {
  return {
    id,
    name: `层-${id}`,
    type: 'vector',
    visible: true,
    opacity: 1,
    group: 'analysis',
    _mapspecLayerId: id,
    ...patch,
  } as Layer;
}

describe('W9 嵌套组 UI + undo 接线', () => {
  beforeEach(() => {
    resetLiveState();
    useHudStore.setState({
      layers: [makeLayer('a'), makeLayer('b')],
      layerGroups: [],
      layerGroupMembership: {},
      lockedLayerIds: [],
      selectedLayerIds: [],
      opsLog: [],
    });
  });

  it('E1: 组头「新建子组」→ 嵌套到该组下（parentId 指向父组）', () => {
    useHudStore.setState({ layerGroups: [{ id: 'wg-root-a', name: '根组', collapsed: false, parentId: null }] });
    render(<LayersTab />);
    fireEvent.click(screen.getByRole('button', { name: '在分组 根组 下新建子组' }));
    const groups = useHudStore.getState().layerGroups;
    const child = groups.find((g) => g.name === '新分组');
    expect(child?.parentId).toBe('wg-root-a');
  });

  it('E1b: 锁定层经全局 Ctrl+Z 反向解锁（键盘路径）', () => {
    // useWorkbenchUndoKeys 挂在 page 级 —— 测试用等价 harness 挂载同一 hook。
    function Harness() {
      useWorkbenchUndoKeys();
      return <LayersTab />;
    }
    render(<Harness />);
    // 锁定
    fireEvent.click(screen.getByRole('button', { name: /锁定图层 层-a/ }));
    expect(useHudStore.getState().lockedLayerIds).toEqual(['a']);
    // 撤销 → 解锁
    fireEvent.keyDown(window, { key: 'z', ctrlKey: true });
    expect(useHudStore.getState().lockedLayerIds).toEqual([]);
  });

  it('E1c: 主头新建分组入 undo 栈（journal 可见）', () => {
    render(<LayersTab />);
    fireEvent.click(screen.getByRole('button', { name: '新建分组' }));
    expect(useHudStore.getState().layerGroups).toHaveLength(1);
    const journal = useHudStore.getState().opsLog;
    expect(journal[0].label).toBe('新建分组');
    expect(journal[0].reversible).toBe(true);
  });
});

describe('W10 provenance 徽标', () => {
  beforeEach(() => {
    resetLiveState();
    useHudStore.setState({
      layers: [
        makeLayer('with-prov', { provenance: { result_ref: 'ref:abc', tool_call_id: 'call-1' } }),
        makeLayer('no-prov'),
      ],
      layerGroups: [],
      layerGroupMembership: {},
      lockedLayerIds: [],
      selectedLayerIds: [],
    });
  });

  it('E2: 有 provenance 的行渲染徽标；点击切到 results tab；无 provenance 不渲染', () => {
    render(<LayersTab />);
    const badge = screen.getByTestId('provenance-badge-with-prov');
    expect(badge).toBeInTheDocument();
    expect(screen.queryByTestId('provenance-badge-no-prov')).toBeNull();
    fireEvent.click(badge);
    expect(useHudStore.getState().activeLeftTab).toBe('results');
  });
});

describe('W12 Escape 退出对比', () => {
  it('E3: 对比激活时 Escape → exitComparison（键盘可达退出）', async () => {
    useHudStore.getState().enterComparison({ primaryLayerId: 'A', secondaryLayerId: 'B' });
    expect(useHudStore.getState().comparison.active).toBe(true);
    const { ComparisonView } = await import('@/components/map/comparison/comparison-view');
    const primaryRef = { current: null };
    render(<ComparisonView primaryMapRef={primaryRef as never} mapStyle={{ version: 8, sources: {}, layers: [] } as never} />);
    fireEvent.keyDown(window, { key: 'Escape' });
    expect(useHudStore.getState().comparison.active).toBe(false);
  });
});
