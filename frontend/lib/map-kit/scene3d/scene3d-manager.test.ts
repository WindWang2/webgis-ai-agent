/**
 * scene3d asset-manager 生命周期测试（G3D02 M4/M7）。
 *
 * fake map（记录 addLayer/removeLayer）+ stub fetch（GLB fixture 供给）；
 * 零 WebGL —— custom layer 由 fake map 收纳不触发渲染。覆盖：幂等差分、
 * revision 换代重挂、2d 卸载、悬空源 typed 降级、dispose 清理。
 */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import type { MapSpec } from '@/lib/mapspec-compiler/types.generated';
import { buildGlbFixture } from './__fixtures__/glb';
import { Scene3DManager } from './asset-manager';
import { resetSceneAssetStatusForTests, sceneAssetStatusSnapshot } from './status';

interface FakeMapCalls {
  addLayer: unknown[];
  removeLayer: string[];
  on: string[];
  off: string[];
  repaints: number;
}

function makeFakeMap() {
  const calls: FakeMapCalls = { addLayer: [], removeLayer: [], on: [], off: [], repaints: 0 };
  const listeners = new Map<string, Set<() => void>>();
  const layers = new Set<string>();
  const map = {
    calls,
    _layers: layers,
    addLayer: (layer: unknown) => {
      calls.addLayer.push(layer);
      const impl = layer as { id?: string };
      if (impl?.id) layers.add(impl.id);
    },
    removeLayer: (id: string) => {
      layers.delete(id);
      calls.removeLayer.push(id);
    },
    getLayer: (id: string) => (layers.has(id) ? { id } : undefined),
    on: (ev: string, h: () => void) => {
      calls.on.push(ev);
      if (!listeners.has(ev)) listeners.set(ev, new Set());
      listeners.get(ev)!.add(h);
    },
    off: (ev: string, h: () => void) => {
      calls.off.push(ev);
      listeners.get(ev)?.delete(h);
    },
    triggerRepaint: () => { calls.repaints += 1; },
    getCanvasContainer: () => ({ clientHeight: 800 }),
    getContainer: () => ({ clientHeight: 800 }),
    queryTerrainElevation: () => null,
    _fire: (ev: string) => { for (const h of listeners.get(ev) ?? []) h(); },
  };
  return map;
}

const GLB_URL = 'https://assets.test/campus.glb';

function specWith(assets: unknown[], mode = '3d'): MapSpec {
  return {
    version: '1.7',
    sources: {
      'model-a': {
        type: 'model3d',
        format: 'glb',
        url: GLB_URL,
        content_revision: 1,
      } as never,
    },
    layers: [],
    scene: { mode, assets: assets as never } as never,
  } as MapSpec;
}

const ASSET = {
  id: 'campus',
  source: 'model-a',
  placement: { anchor: [104.06, 30.67], altitude_m: 500 },
  visible: true,
};

let fetchMock: ReturnType<typeof vi.fn>;

beforeEach(() => {
  resetSceneAssetStatusForTests();
  fetchMock = vi.fn(async (url: string) => {
    if (url === GLB_URL) {
      const buf = buildGlbFixture({ triangles: 2 });
      return new Response(buf, { status: 200, headers: { 'content-length': String(buf.byteLength) } });
    }
    return new Response('not found', { status: 404 });
  });
  vi.stubGlobal('fetch', fetchMock);
});

afterEach(() => {
  vi.unstubAllGlobals();
  resetSceneAssetStatusForTests();
});

async function settle(ms = 20): Promise<void> {
  await new Promise((r) => setTimeout(r, ms));
}

describe('Scene3DManager.sync', () => {
  it('mounts an asset (custom layer + fetch + build)', async () => {
    const map = makeFakeMap();
    const mgr = new Scene3DManager(map as never);
    const summary = mgr.sync(specWith([ASSET]));
    expect(summary.mounted).toBe(1);
    await settle();
    expect(map.calls.addLayer).toHaveLength(1);
    const layer = map.calls.addLayer[0] as { id: string; type: string; renderingMode: string };
    expect(layer.id).toBe('scene3d-campus');
    expect(layer.type).toBe('custom');
    expect(layer.renderingMode).toBe('3d');
    expect(fetchMock).toHaveBeenCalledWith(GLB_URL, expect.anything());
    const statuses = sceneAssetStatusSnapshot();
    expect(statuses.some((e) => e.assetId === 'campus' && e.code === 'mounted')).toBe(true);
    mgr.dispose();
  });

  it('is idempotent: re-sync with identical spec does not remount', async () => {
    const map = makeFakeMap();
    const mgr = new Scene3DManager(map as never);
    const spec = specWith([ASSET]);
    mgr.sync(spec);
    await settle();
    const s2 = mgr.sync(spec);
    expect(s2).toEqual({ mounted: 0, removed: 0, updated: 0 });
    expect(map.calls.addLayer).toHaveLength(1);
    expect(fetchMock).toHaveBeenCalledTimes(1);
    mgr.dispose();
  });

  it('revision change remounts (source content_revision bump)', async () => {
    const map = makeFakeMap();
    const mgr = new Scene3DManager(map as never);
    mgr.sync(specWith([ASSET]));
    await settle();
    const spec2 = specWith([ASSET]);
    (spec2.sources['model-a'] as { content_revision: number }).content_revision = 2;
    const s2 = mgr.sync(spec2);
    expect(s2.removed).toBe(1);
    expect(s2.mounted).toBe(1);
    await settle();
    expect(map.calls.addLayer.length).toBeGreaterThanOrEqual(2);
    expect(map.calls.removeLayer).toContain('scene3d-campus');
    mgr.dispose();
  });

  it('visibility toggle updates without remount', async () => {
    const map = makeFakeMap();
    const mgr = new Scene3DManager(map as never);
    mgr.sync(specWith([ASSET]));
    await settle();
    const s2 = mgr.sync(specWith([{ ...ASSET, visible: false }]));
    expect(s2.updated).toBe(1);
    expect(s2.mounted).toBe(0);
    expect(map.calls.addLayer).toHaveLength(1);
    mgr.dispose();
  });

  it('asset removed from spec unmounts', async () => {
    const map = makeFakeMap();
    const mgr = new Scene3DManager(map as never);
    mgr.sync(specWith([ASSET]));
    await settle();
    mgr.sync(specWith([]));
    expect(map.calls.removeLayer).toContain('scene3d-campus');
    expect(mgr.describe()).toEqual([]);
    mgr.dispose();
  });

  it('2d scene mode unmounts all assets (honest degrade)', async () => {
    const map = makeFakeMap();
    const mgr = new Scene3DManager(map as never);
    mgr.sync(specWith([ASSET]));
    await settle();
    mgr.sync(specWith([ASSET], '2d'));
    expect(map.calls.removeLayer).toContain('scene3d-campus');
    mgr.dispose();
  });

  it('dangling/non-model3d source records typed unavailable', () => {
    const map = makeFakeMap();
    const mgr = new Scene3DManager(map as never);
    const bad = specWith([{ ...ASSET, source: 'nope' }]);
    const summary = mgr.sync(bad);
    expect(summary.mounted).toBe(0);
    const statuses = sceneAssetStatusSnapshot();
    expect(statuses.some((e) => e.code === 'unavailable' && e.reason === 'source_missing_or_not_model3d')).toBe(true);
    mgr.dispose();
  });

  it('fetch 404 → load_failed recorded and layer cleaned up', async () => {
    const map = makeFakeMap();
    const mgr = new Scene3DManager(map as never);
    const badUrlSpec = specWith([ASSET]);
    (badUrlSpec.sources['model-a'] as { url: string }).url = 'https://assets.test/gone.glb';
    mgr.sync(badUrlSpec);
    await settle();
    const statuses = sceneAssetStatusSnapshot();
    expect(statuses.some((e) => e.code === 'load_failed' && e.assetId === 'campus')).toBe(true);
    expect(map.calls.removeLayer).toContain('scene3d-campus');
    mgr.dispose();
  });

  it('ref_id-only source is typed unavailable (fabric hydration boundary)', async () => {
    const map = makeFakeMap();
    const mgr = new Scene3DManager(map as never);
    const refSpec = specWith([ASSET]);
    const src = refSpec.sources['model-a'] as { url?: string; ref_id?: string };
    delete src.url;
    src.ref_id = 'ref:model-1';
    mgr.sync(refSpec);
    await settle();
    const statuses = sceneAssetStatusSnapshot();
    expect(statuses.some((e) => e.code === 'load_failed' && /hydration/.test(e.reason ?? ''))).toBe(true);
    mgr.dispose();
  });

  it('move listeners attach on first mount and detach after last unmount', async () => {
    const map = makeFakeMap();
    const mgr = new Scene3DManager(map as never);
    mgr.sync(specWith([ASSET]));
    await settle();
    expect(map.calls.on.filter((e) => e === 'move')).toHaveLength(1);
    mgr.sync(specWith([]));
    expect(map.calls.off.filter((e) => e === 'move')).toHaveLength(1);
    mgr.dispose();
  });

  it('basemap setStyle sweep (layer missing) forces remount', async () => {
    const map = makeFakeMap();
    const mgr = new Scene3DManager(map as never);
    const spec = specWith([ASSET]);
    mgr.sync(spec);
    await settle();
    // setStyle 非 diff 回退抹层且不回调 onRemove —— sync 必须重挂。
    (map as unknown as { _layers: Set<string> })._layers?.clear?.();
    const s2 = mgr.sync(spec);
    expect(s2.mounted).toBe(1);
    await settle();
    expect(map.calls.addLayer.length).toBeGreaterThanOrEqual(2);
    mgr.dispose();
  });

  it('dispose unmounts everything', async () => {
    const map = makeFakeMap();
    const mgr = new Scene3DManager(map as never);
    mgr.sync(specWith([ASSET]));
    await settle();
    mgr.dispose();
    expect(map.calls.removeLayer).toContain('scene3d-campus');
    expect(mgr.describe()).toEqual([]);
    // 二次 dispose 幂等
    mgr.dispose();
  });
});
