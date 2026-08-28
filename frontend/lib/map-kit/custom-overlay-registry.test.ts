import { beforeEach, describe, expect, it, vi } from 'vitest';
import {
  clearCustomOverlayRegistry,
  listCustomOverlayLayerIds,
  recordCustomOverlayLayer,
  recordCustomOverlaySource,
  remountCustomOverlays,
  unregisterCustomOverlay,
} from './custom-overlay-registry';
import { CUSTOM_OVERLAY_PREFIX } from './renderer';

/**
 * #1078 FE1（v2 Phase 5）：Runtime Layer Mount Registry 契约。
 * setStyle 重载抹掉 custom-* 覆盖层后，注册表按插入序重放；
 * 删除的覆盖层不复活（反注册）；会话切换清空。
 */

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

const FC = { type: 'FeatureCollection', features: [] };

describe('custom-overlay-registry', () => {
  beforeEach(() => {
    clearCustomOverlayRegistry();
  });

  it('非 custom- 前缀的挂载不记账（spec 承载层由 reconcile 重放）', () => {
    recordCustomOverlaySource('plain-source', { kind: 'geojson', data: FC });
    recordCustomOverlayLayer({ id: 'L1__main', type: 'circle', source: 'plain-source' });
    expect(listCustomOverlayLayerIds()).toEqual([]);
  });

  it('remount 按插入序重放 sources→layers，幂等且可观测', () => {
    recordCustomOverlaySource(`${CUSTOM_OVERLAY_PREFIX}a`, { kind: 'geojson', data: FC });
    recordCustomOverlayLayer({
      id: `${CUSTOM_OVERLAY_PREFIX}a`, type: 'circle',
      source: `${CUSTOM_OVERLAY_PREFIX}a`, paint: { 'circle-color': '#f00' },
    });
    const { map, calls } = fakeMap();
    const n1 = remountCustomOverlays(map);
    expect(n1).toBe(1);
    expect(calls).toEqual([`source:${CUSTOM_OVERLAY_PREFIX}a`, `layer:${CUSTOM_OVERLAY_PREFIX}a`]);
    // 幂等：已存在的不再重复挂
    calls.length = 0;
    expect(remountCustomOverlays(map)).toBe(0);
    expect(calls).toEqual([]);
  });

  it('反注册的覆盖层在 remount 后不复活（无 layer resurrection）', () => {
    recordCustomOverlaySource(`${CUSTOM_OVERLAY_PREFIX}a`, { kind: 'geojson', data: FC });
    recordCustomOverlayLayer({ id: `${CUSTOM_OVERLAY_PREFIX}a`, type: 'circle', source: `${CUSTOM_OVERLAY_PREFIX}a` });
    recordCustomOverlayLayer({ id: `${CUSTOM_OVERLAY_PREFIX}b`, type: 'fill', source: `${CUSTOM_OVERLAY_PREFIX}b-src` });
    recordCustomOverlaySource(`${CUSTOM_OVERLAY_PREFIX}b-src`, { kind: 'geojson', data: FC });

    unregisterCustomOverlay(`${CUSTOM_OVERLAY_PREFIX}a`);
    const { map, layers } = fakeMap();
    remountCustomOverlays(map);
    expect(layers.has(`${CUSTOM_OVERLAY_PREFIX}a`)).toBe(false);
    expect(layers.has(`${CUSTOM_OVERLAY_PREFIX}b`)).toBe(true);
  });

  it('image 源重放携带 url + coordinates', () => {
    const coords = [[0, 1], [1, 1], [1, 0], [0, 0]] as any;
    recordCustomOverlaySource(`${CUSTOM_OVERLAY_PREFIX}img`, { kind: 'image', url: 'http://x/t.png', coordinates: coords });
    recordCustomOverlayLayer({ id: `${CUSTOM_OVERLAY_PREFIX}img-layer`, type: 'raster', source: `${CUSTOM_OVERLAY_PREFIX}img` });
    const { map, sources } = fakeMap();
    remountCustomOverlays(map);
    expect(sources.get(`${CUSTOM_OVERLAY_PREFIX}img`)).toEqual({
      type: 'image', url: 'http://x/t.png', coordinates: coords,
    });
  });

  it('remount 容错：addSource 抛错不阻断后续重放', () => {
    recordCustomOverlaySource(`${CUSTOM_OVERLAY_PREFIX}bad`, { kind: 'geojson', data: FC });
    recordCustomOverlayLayer({ id: `${CUSTOM_OVERLAY_PREFIX}bad`, type: 'circle', source: `${CUSTOM_OVERLAY_PREFIX}bad` });
    recordCustomOverlaySource(`${CUSTOM_OVERLAY_PREFIX}good`, { kind: 'geojson', data: FC });
    recordCustomOverlayLayer({ id: `${CUSTOM_OVERLAY_PREFIX}good`, type: 'circle', source: `${CUSTOM_OVERLAY_PREFIX}good` });
    const { map, layers } = fakeMap();
    const orig = map.addSource;
    map.addSource = (id: string, def: any) => {
      if (id === `${CUSTOM_OVERLAY_PREFIX}bad`) throw new Error('style not loaded');
      orig(id, def);
    };
    expect(() => remountCustomOverlays(map)).not.toThrow();
    expect(layers.has(`${CUSTOM_OVERLAY_PREFIX}good`)).toBe(true);
  });
});

describe('custom-overlay-registry — 会话切换清理（review 5/6-B8）', () => {
  it('setMapSpecSessionCursor 会话 id 变化清空挂载账本', async () => {
    recordCustomOverlaySource(`${CUSTOM_OVERLAY_PREFIX}a`, { kind: 'geojson', data: FC });
    recordCustomOverlayLayer({ id: `${CUSTOM_OVERLAY_PREFIX}a`, type: 'circle', source: `${CUSTOM_OVERLAY_PREFIX}a` });
    const { setMapSpecSessionCursor } = await import('@/lib/mapspec/session-cursor');
    setMapSpecSessionCursor('session-b');  // id 变化 → 动态 import 清账本
    await new Promise((r) => setTimeout(r, 20));  // 等 best-effort 微任务
    expect(listCustomOverlayLayerIds()).toEqual([]);
    const { map, layers } = fakeMap();
    expect(remountCustomOverlays(map)).toBe(0);
    expect(layers.size).toBe(0);
    clearCustomOverlayRegistry();
  });
});
