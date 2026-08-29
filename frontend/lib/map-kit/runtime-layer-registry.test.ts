import { beforeEach, describe, expect, it } from 'vitest';
import {
  clearRuntimeLayerRegistry,
  describeRuntimeLayers,
  listRuntimeLayerIds,
  recordRuntimeLayer,
  recordRuntimeSource,
  registerRuntimeLayer,
  rememberRuntimeRemount,
  remountRuntimeLayers,
  resetRuntimeLayerRegistry,
  runtimeLayerCount,
  unregisterRuntimeLayer,
} from './runtime-layer-registry';
import {
  clearCustomOverlayRegistry,
  listCustomOverlayLayerIds,
  recordCustomOverlayLayer,
  recordCustomOverlaySource,
} from './custom-overlay-registry';
import {
  forgetCustomOverlay,
  rememberedCustomOverlayCount,
  rememberCustomOverlay,
  resetCustomOverlayRegistry,
} from '../map-commands/custom-overlay-registry';

/**
 * Unified Map Runtime Layer Registry 契约（Runtime v3 Phase B, ADR-0080）。
 *
 * 锁定：
 * - 单一事实源：两个旧 facade（map-kit 定义账本 / map-commands 闭包账本）
 *   读写同一 canonical 存储；
 * - 描述符：family 从 layer type 派生；ownership/mountMode/persistence
 *   契约边界（command/imperative/session）；
 * - 重放：sources→layers 插入序、幂等、hooks.onLayerAdded、闭包兜底
 *   （无 layerDef 时才触发）、native heatmap 定义在账（v2 缺失修复）；
 * - 生命周期：反注册不复活、会话清空、有界 FIFO；
 * - spec 层不进账本（declarative 真相在 MapSpec reconcile）。
 */

const FC = { type: 'FeatureCollection', features: [] };

function fakeMap() {
  const layers = new Map<string, any>();
  const sources = new Map<string, any>();
  const calls: string[] = [];
  const map: any = {
    getLayer: (id: string) => layers.get(id),
    getSource: (id: string) => sources.get(id),
    addLayer: (def: any) => { layers.set(def.id, def); calls.push(`layer:${def.id}`); },
    addSource: (id: string, def: any) => { sources.set(id, def); calls.push(`source:${id}`); },
  };
  return { map, layers, sources, calls };
}

describe('runtime-layer-registry — canonical 存储', () => {
  beforeEach(() => resetRuntimeLayerRegistry());

  it('两个 facade 写同一存储（无第二账本）', () => {
    recordCustomOverlaySource('custom-a', { kind: 'geojson', data: FC });
    recordCustomOverlayLayer({ id: 'custom-a', type: 'circle', source: 'custom-a' });
    rememberCustomOverlay('custom-b', () => {});
    expect(runtimeLayerCount()).toBe(2);
    // 旧观测面看到同一批条目
    expect(listCustomOverlayLayerIds()).toEqual(['custom-a']);
    expect(rememberedCustomOverlayCount()).toBe(1);
  });

  it('任一 facade 的清空都清 canonical 存储', () => {
    recordCustomOverlayLayer({ id: 'custom-a', type: 'circle', source: 'custom-a' });
    rememberCustomOverlay('custom-b', () => {});
    clearCustomOverlayRegistry();
    expect(runtimeLayerCount()).toBe(0);
    recordCustomOverlayLayer({ id: 'custom-c', type: 'circle', source: 'custom-c' });
    resetCustomOverlayRegistry();
    expect(runtimeLayerCount()).toBe(0);
  });

  it('描述符：family 派生 + ownership/mountMode/persistence 契约', () => {
    recordRuntimeSource('custom-heat', { kind: 'geojson', data: FC });
    recordRuntimeLayer({ id: 'custom-heat', type: 'heatmap', source: 'custom-heat' });
    recordRuntimeLayer({ id: 'custom-ras-layer', type: 'raster', source: 'custom-ras' });
    recordRuntimeLayer({ id: 'custom-vec', type: 'circle', source: 'custom-vec' });
    const byId = new Map(describeRuntimeLayers().map((e) => [e.runtimeLayerId, e]));
    expect(byId.get('custom-heat')?.family).toBe('heatmap');
    expect(byId.get('custom-ras-layer')?.family).toBe('raster');
    expect(byId.get('custom-vec')?.family).toBe('vector');
    for (const entry of describeRuntimeLayers()) {
      expect(entry.ownership).toBe('command');
      expect(entry.mountMode).toBe('imperative');
      expect(entry.persistence).toBe('session');
      expect(entry.zGroup).toBe(0);
      expect(entry.seq).toBeGreaterThan(0);
    }
  });

  it('source 先行建账，层到达时升级为层键（单条目）', () => {
    recordRuntimeSource('custom-x', { kind: 'geojson', data: FC });
    expect(runtimeLayerCount()).toBe(1);
    recordRuntimeLayer({ id: 'custom-x-layer', type: 'circle', source: 'custom-x' });
    const entries = describeRuntimeLayers();
    expect(entries).toHaveLength(1);
    expect(entries[0].runtimeLayerId).toBe('custom-x-layer');
    expect(entries[0].sourceId).toBe('custom-x');
    expect(entries[0].sourceDef?.kind).toBe('geojson');
    expect(entries[0].layerDef?.type).toBe('circle');
  });

  it('重放：先 sources 后 layers、按插入序、幂等、onLayerAdded hook', () => {
    recordRuntimeSource('custom-1', { kind: 'geojson', data: FC });
    recordRuntimeLayer({ id: 'custom-1', type: 'circle', source: 'custom-1' });
    recordRuntimeSource('custom-2', { kind: 'geojson', data: FC });
    recordRuntimeLayer({ id: 'custom-2', type: 'fill', source: 'custom-2' });
    const { map, calls } = fakeMap();
    const noted: string[] = [];
    const n = remountRuntimeLayers(map, { onLayerAdded: (_m, id) => noted.push(id) });
    expect(n).toBe(2);
    expect(calls).toEqual([
      'source:custom-1', 'source:custom-2',
      'layer:custom-1', 'layer:custom-2',
    ]);
    expect(noted).toEqual(['custom-1', 'custom-2']);
    calls.length = 0;
    expect(remountRuntimeLayers(map)).toBe(0);
    expect(calls).toEqual([]);
  });

  it('闭包兜底：仅无 layerDef 的条目走闭包重放', () => {
    recordRuntimeSource('custom-def', { kind: 'geojson', data: FC });
    recordRuntimeLayer({ id: 'custom-def', type: 'circle', source: 'custom-def' });
    const closureIds: string[] = [];
    rememberRuntimeRemount('custom-def', () => closureIds.push('def-entry'));
    rememberRuntimeRemount('custom-closure-only', () => closureIds.push('closure-only'));
    const { map, layers } = fakeMap();
    const n = remountRuntimeLayers(map);
    expect(n).toBe(2);
    expect(closureIds).toEqual(['closure-only']);
    expect(layers.has('custom-def')).toBe(true);
    expect(layers.has('custom-closure-only')).toBe(false);
  });

  it('反注册：层族前缀清扫 + source 一并移除，不复活', () => {
    recordRuntimeSource('custom-x', { kind: 'geojson', data: FC });
    recordRuntimeLayer({ id: 'custom-x-layer', type: 'raster', source: 'custom-x' });
    unregisterRuntimeLayer('custom-x');
    expect(runtimeLayerCount()).toBe(0);
    const { map, sources } = fakeMap();
    remountRuntimeLayers(map);
    expect(sources.has('custom-x')).toBe(false);
  });

  it('forgetCustomOverlay（facade）与 unregister 同存储语义', () => {
    recordRuntimeSource('custom-y', { kind: 'geojson', data: FC });
    recordRuntimeLayer({ id: 'custom-y', type: 'circle', source: 'custom-y' });
    forgetCustomOverlay('custom-y');
    expect(runtimeLayerCount()).toBe(0);
  });

  it('有界 FIFO：256 上限，驱逐最旧', () => {
    for (let i = 0; i < 300; i++) {
      recordRuntimeLayer({ id: `custom-l${i}`, type: 'circle', source: `custom-l${i}` });
    }
    expect(runtimeLayerCount()).toBe(256);
    const ids = listRuntimeLayerIds();
    expect(ids).not.toContain('custom-l0');
    expect(ids).toContain('custom-l299');
  });

  it('会话切换清空（clearRuntimeLayerRegistry）', () => {
    recordRuntimeLayer({ id: 'custom-s1', type: 'circle', source: 'custom-s1' });
    clearRuntimeLayerRegistry();
    const { map, layers } = fakeMap();
    expect(remountRuntimeLayers(map)).toBe(0);
    expect(layers.size).toBe(0);
  });
});

describe('runtime-layer-registry — native heatmap 定义在账（v2 缺失修复）', () => {
  beforeEach(() => resetRuntimeLayerRegistry());

  it('addNativeHeatmap 挂载后 layer 定义可重放', async () => {
    const renderer = await import('./renderer');
    const { makeMockMaplibreMap } = await import('../../test/__mocks__/maplibre-map');
    const mapAny = makeMockMaplibreMap() as any;
    // source 经挂载缝（addGeoJsonSource 记账）
    renderer.addGeoJsonSource(mapAny, 'custom-heat-1', FC);
    renderer.addNativeHeatmap(mapAny, {
      id: 'custom-heat-1', source: 'custom-heat-1',
    });
    const ids = listRuntimeLayerIds();
    expect(ids).toContain('custom-heat-1');
    const entry = describeRuntimeLayers().find(
      (e) => e.runtimeLayerId === 'custom-heat-1',
    );
    expect(entry?.family).toBe('heatmap');
    expect(entry?.layerDef?.type).toBe('heatmap');
    expect(entry?.layerDef?.paint).toBeTruthy();
    // 重放：从零样式恢复 source + heatmap layer
    const wiped = makeMockMaplibreMap() as any;
    const n = remountRuntimeLayers(wiped);
    expect(n).toBe(1);
    expect(wiped.getLayer('custom-heat-1')?.type).toBe('heatmap');
    expect(wiped.getSource('custom-heat-1')).toBeTruthy();
  });
});

describe('runtime-layer-registry — registerRuntimeLayer 直接登记', () => {
  beforeEach(() => resetRuntimeLayerRegistry());

  it('显式 family 覆盖派生值；upsert 保持首插位次', () => {
    registerRuntimeLayer({ id: 'custom-k', sourceId: 'custom-k', family: 'annotation' });
    expect(describeRuntimeLayers()[0].family).toBe('annotation');
    recordRuntimeLayer({ id: 'custom-a', type: 'circle', source: 'custom-a' });
    recordRuntimeLayer({ id: 'custom-b', type: 'circle', source: 'custom-b' });
    // upsert a：仍在首位
    recordRuntimeLayer({ id: 'custom-a', type: 'fill', source: 'custom-a' });
    expect(listRuntimeLayerIds()).toEqual(['custom-a', 'custom-b']);
  });

  it('review-A/B/C：同一 source 上的第二个层不得吸附/删除第一个层的账目', () => {
    recordRuntimeSource('custom-shared', { kind: 'geojson', data: FC });
    recordRuntimeLayer({ id: 'custom-fill', type: 'fill', source: 'custom-shared' });
    recordRuntimeLayer({ id: 'custom-line', type: 'line', source: 'custom-shared' });
    const ids = listRuntimeLayerIds();
    expect(ids).toContain('custom-fill');
    expect(ids).toContain('custom-line');
    expect(runtimeLayerCount()).toBe(2);
    // 重放：source 一次 + 两个层都恢复
    const { map, layers, sources } = fakeMap();
    expect(remountRuntimeLayers(map)).toBe(2);
    expect(sources.has('custom-shared')).toBe(true);
    expect(layers.has('custom-fill')).toBe(true);
    expect(layers.has('custom-line')).toBe(true);
    // 反注册其中一个层不误伤另一个
    unregisterRuntimeLayer('custom-fill');
    expect(listRuntimeLayerIds()).toEqual(['custom-line']);
  });
});

describe('runtime-layer-registry — 规模与边界契约（确定性，非 wall-clock）', () => {
  beforeEach(() => resetRuntimeLayerRegistry());

  function fillLayers(n: number): void {
    for (let i = 0; i < n; i++) {
      recordRuntimeSource(`custom-p${i}`, { kind: 'geojson', data: FC });
      recordRuntimeLayer({ id: `custom-p${i}`, type: 'circle', source: `custom-p${i}` });
    }
  }

  it('100 层：登记 O(1)/层（无 O(N²) 吸附扫描），全量重放恢复 100 层', () => {
    fillLayers(100);
    expect(runtimeLayerCount()).toBe(100);
    const { map, layers } = fakeMap();
    const n = remountRuntimeLayers(map);
    expect(n).toBe(100);
    expect(layers.size).toBe(100);
  });

  it('300 层：有界 256，满容量重放恢复全部在账层', () => {
    fillLayers(300);
    expect(runtimeLayerCount()).toBe(256);
    const { map, layers } = fakeMap();
    expect(remountRuntimeLayers(map)).toBe(256);
    expect(layers.size).toBe(256);
  });

  it('重复 style reload：重放幂等且不泄漏（反复全量重放）', () => {
    fillLayers(120);
    for (let round = 0; round < 10; round++) {
      const { map, layers } = fakeMap(); // 每轮模拟一次 setStyle 清空
      expect(remountRuntimeLayers(map)).toBe(120);
      expect(layers.size).toBe(120);
      expect(runtimeLayerCount()).toBe(120);
    }
  });

  it('会话切换清账后旧层不泄漏进新会话重放', () => {
    fillLayers(80);
    clearRuntimeLayerRegistry();
    fillLayers(10);
    const { map, layers } = fakeMap();
    expect(remountRuntimeLayers(map)).toBe(10);
    expect(layers.size).toBe(10);
    for (const id of layers.keys()) {
      expect(id.startsWith('custom-p')).toBe(true);
      const idx = Number(id.slice('custom-p'.length));
      expect(idx).toBeLessThan(10);
    }
  });
});
