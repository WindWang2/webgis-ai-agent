/**
 * Agent lock gate（Workbench V5 / W2）行为测试。
 *
 * 锁定 = 用户意图护栏，必须同时约束 agent 的可见性事务与 remove_layer
 * 通道（V4 只护 UI 面板 —— 自认缺口）。typed 冲突：status:'failed' +
 * error:'layer_locked' + result.locked_layer_ids；用户解锁是唯一 override。
 */
import { beforeEach, describe, expect, it, vi } from 'vitest';
import {
  commitMapSpecDocument,
  resetLiveState,
} from '@/lib/mapspec/session-cursor';
import { useHudStore } from '@/lib/store/useHudStore';
import { makeMockMaplibreMap } from '@/test/__mocks__/maplibre-map';
import { layerCommands } from '@/lib/map-commands/layerCommands';
import { LOCK_CONFLICT_ERROR, partitionByLock } from './layer-lock';
import type { MapCommandContext } from '@/lib/map-commands/types';

function makeCtx(
  params: Record<string, unknown>,
  layers: any[],
  map = makeMockMaplibreMap(),
): MapCommandContext {
  const updateLayer = vi.fn();
  return {
    map,
    popAction: () => {},
    setDeferredPop: () => {},
    safePop: () => {},
    getHudState: () => ({ layers, updateLayer, lockedLayerIds: useHudStore.getState().lockedLayerIds }),
    setSelectedBaseLayer: () => {},
    command: 'lock-gate-test',
    params,
  } as unknown as MapCommandContext;
}

describe('partitionByLock（纯函数）', () => {
  it('按锁定集分区且保持入参序', () => {
    expect(partitionByLock(['a', 'b', 'c'], ['b'])).toEqual({
      allowed: ['a', 'c'],
      locked: ['b'],
    });
    expect(partitionByLock([], ['b'])).toEqual({ allowed: [], locked: [] });
  });
});

describe('agent 可见性事务 lock 门', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    resetLiveState();
    useHudStore.setState({ lockedLayerIds: [] });
  });

  it('锁定层可见性突变 → typed layer_locked 冲突，store/runtime 不动', () => {
    useHudStore.setState({ lockedLayerIds: ['result'] });
    const map = makeMockMaplibreMap();
    map.addLayer({ id: 'result__fill', type: 'fill' });
    map.setLayoutProperty('result__fill', 'visibility', 'visible');
    const updateLayer = vi.fn();
    const ctx = {
      map,
      popAction: () => {},
      setDeferredPop: () => {},
      safePop: () => {},
      getHudState: () => ({ layers: [{ id: 'result', visible: true }], updateLayer }),
      setSelectedBaseLayer: () => {},
      command: 'layer_visibility_update',
      params: { layer_id: 'result', visible: false },
    } as unknown as MapCommandContext;

    const result = layerCommands.layer_visibility_update.run(ctx) as any;
    expect(result.status).toBe('failed');
    expect(result.error).toBe(LOCK_CONFLICT_ERROR);
    expect(result.result?.locked_layer_ids).toEqual(['result']);
    // 锁定即拒绝：store 乐观更新与 runtime 突变都不得发生
    expect(updateLayer).not.toHaveBeenCalled();
    expect(map.getLayoutProperty('result__fill', 'visibility')).toBe('visible');
  });

  it('用户解锁后同一突变放行（override = 显式解锁）', () => {
    const map = makeMockMaplibreMap();
    map.addLayer({ id: 'result__fill', type: 'fill' });
    map.setLayoutProperty('result__fill', 'visibility', 'visible');
    const ctx = makeCtx(
      { layer_id: 'result', visible: false },
      [{ id: 'result', visible: true, group: 'analysis' }],
      map,
    );
    const result = layerCommands.layer_visibility_update.run(ctx) as any;
    expect(result.status).toBe('succeeded');
    expect(result.result?.confirmed).toBe(true);
    expect(result.result?.locked_layer_ids).toBeUndefined();
  });

  it('多目标部分被锁：只应用未锁目标并披露 locked_layer_ids', () => {
    commitMultiRefSpec();
    useHudStore.setState({ lockedLayerIds: ['product-heat'] });    const map = makeMockMaplibreMap();
    for (const id of ['product-heat__circle', 'product-points__circle']) {
      map.addLayer({ id, type: 'circle' });
      map.setLayoutProperty(id, 'visibility', 'visible');
    }
    const ctx = makeCtx({ layer_id: 'ref:geojson-multi', visible: false }, [], map);
    const result = layerCommands.layer_visibility_update.run(ctx) as any;
    expect(result.status).toBe('succeeded');
    expect(result.result?.locked_layer_ids).toEqual(['product-heat']);
    expect(result.result?.target_ids).toEqual(['product-points']);
    // 被锁目标的 runtime 层不被触碰
    expect(map.getLayoutProperty('product-heat__circle', 'visibility')).toBe('visible');
    expect(map.getLayoutProperty('product-points__circle', 'visibility')).toBe('none');
  });
});

function commitMultiRefSpec() {
  commitMapSpecDocument({
    version: '1.0',
    sources: { src1: { type: 'geojson', ref_id: 'ref:geojson-multi' } },
    layers: [
      { id: 'product-heat', source: 'src1', type: 'heatmap' },
      { id: 'product-points', source: 'src1', type: 'circle' },
    ],
  } as any);
}

describe('agent remove_layer lock 门', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    resetLiveState();
    useHudStore.setState({ lockedLayerIds: [] });
  });

  it('删除锁定层 → typed layer_locked 冲突，不发生任何删除', () => {
    useHudStore.setState({ lockedLayerIds: ['guard-me'] });
    const map = makeMockMaplibreMap();
    map.addLayer({ id: 'guard-me__fill', type: 'fill' });
    const removeLayer = vi.fn();
    const ctx = {
      map,
      popAction: () => {},
      setDeferredPop: () => {},
      safePop: () => {},
      getHudState: () => ({
        layers: [{ id: 'guard-me', visible: true }],
        updateLayer: vi.fn(),
        removeLayer,
      }),
      setSelectedBaseLayer: () => {},
      command: 'remove_layer',
      params: { layer_id: 'guard-me' },
    } as unknown as MapCommandContext;

    const result = layerCommands.remove_layer.run(ctx) as any;
    expect(result.status).toBe('failed');
    expect(result.error).toBe(LOCK_CONFLICT_ERROR);
    expect(result.result?.locked_layer_ids).toEqual(['guard-me']);
    expect(removeLayer).not.toHaveBeenCalled();
    expect(map.getLayer('guard-me__fill')).toBeTruthy();
  });

  it('未锁层删除不受影响', () => {
    const map = makeMockMaplibreMap();
    map.addLayer({ id: 'free__fill', type: 'fill' });
    const removeLayer = vi.fn();
    const ctx = {
      map,
      popAction: () => {},
      setDeferredPop: () => {},
      safePop: () => {},
      getHudState: () => ({
        layers: [{ id: 'free', visible: true }],
        updateLayer: vi.fn(),
        removeLayer,
      }),
      setSelectedBaseLayer: () => {},
      command: 'remove_layer',
      params: { layer_id: 'free' },
    } as unknown as MapCommandContext;

    const result = layerCommands.remove_layer.run(ctx) as any;
    expect(result.status).toBe('succeeded');
    expect(removeLayer).toHaveBeenCalledWith('free');
  });
});
