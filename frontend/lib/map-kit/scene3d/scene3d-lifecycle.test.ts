/**
 * scene3d 生命周期终态与 WebGL context lost/restored 测试（G3DV2-B M4）。
 *
 * 覆盖：cancelled（在途加载被打断）与 unmounted（落定后移除）区分；
 * context lost 停加载/释放 GPU 侧/状态标记；restored 有限重建（重挂 +
 * 状态链 context_lost → unmounted → loading → mounted）；lost 期间 sync
 * 守卫。
 */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import type { MapSpec } from '@/lib/mapspec-compiler/types.generated';
import { buildGlbFixture } from './__fixtures__/glb';
import { Scene3DManager } from './asset-manager';
import { resetSceneAssetStatusForTests, sceneAssetStatusSnapshot } from './status';

function makeFakeMap() {
  const layers = new Set<string>();
  const map = {
    calls: { addLayer: [] as unknown[], removeLayer: [] as string[] },
    _layers: layers,
    addLayer: (layer: unknown) => {
      map.calls.addLayer.push(layer);
      const impl = layer as { id?: string };
      if (impl?.id) layers.add(impl.id);
    },
    removeLayer: (id: string) => { layers.delete(id); map.calls.removeLayer.push(id); },
    getLayer: (id: string) => (layers.has(id) ? { id } : undefined),
    on: () => {},
    off: () => {},
    triggerRepaint: () => {},
    getCanvasContainer: () => ({ clientHeight: 800 }),
    getContainer: () => ({ clientHeight: 800 }),
    queryTerrainElevation: () => null,
  };
  return map;
}

const GLB_URL = 'https://assets.test/campus.glb';

function specWith(assets: unknown[], mode = '3d'): MapSpec {
  return {
    version: '1.7',
    sources: { 'model-a': { type: 'model3d', format: 'glb', url: GLB_URL, content_revision: 1 } as never },
    layers: [],
    scene: { mode, assets: assets as never } as never,
  } as MapSpec;
}

const ASSET = { id: 'campus', source: 'model-a', placement: { anchor: [104.06, 30.67], altitude_m: 500 }, visible: true };

beforeEach(() => {
  resetSceneAssetStatusForTests();
});

afterEach(() => {
  vi.unstubAllGlobals();
  resetSceneAssetStatusForTests();
});

async function settle(ms = 20): Promise<void> {
  await new Promise((r) => setTimeout(r, ms));
}

describe('terminal status distinction', () => {
  it('records cancelled when unmounted mid-load (in-flight abort)', async () => {
    // fetch 永不落定 → 加载在途 → sync([]) 打断 = cancelled
    vi.stubGlobal('fetch', vi.fn(() => new Promise<Response>(() => {})));
    const map = makeFakeMap();
    const mgr = new Scene3DManager(map as never);
    mgr.sync(specWith([ASSET]));
    await settle(5);
    mgr.sync(specWith([]));
    const statuses = sceneAssetStatusSnapshot();
    expect(statuses.some((e) => e.assetId === 'campus' && e.code === 'cancelled')).toBe(true);
    expect(statuses.some((e) => e.assetId === 'campus' && e.code === 'unmounted')).toBe(false);
    mgr.dispose();
  });

  it('records unmounted (not cancelled) after load settled', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => {
      const buf = buildGlbFixture({ triangles: 1 });
      return new Response(buf, { status: 200, headers: { 'content-length': String(buf.byteLength) } });
    }));
    const map = makeFakeMap();
    const mgr = new Scene3DManager(map as never);
    mgr.sync(specWith([ASSET]));
    await settle();
    mgr.sync(specWith([]));
    const statuses = sceneAssetStatusSnapshot();
    expect(statuses.some((e) => e.assetId === 'campus' && e.code === 'unmounted')).toBe(true);
    expect(statuses.some((e) => e.assetId === 'campus' && e.code === 'cancelled')).toBe(false);
    mgr.dispose();
  });
});

describe('WebGL context lost / restored', () => {
  beforeEach(() => {
    vi.stubGlobal('fetch', vi.fn(async () => {
      const buf = buildGlbFixture({ triangles: 1 });
      return new Response(buf, { status: 200, headers: { 'content-length': String(buf.byteLength) } });
    }));
  });

  it('context lost: aborts in-flight, frees GPU side, marks stale; sync guarded', async () => {
    const map = makeFakeMap();
    const mgr = new Scene3DManager(map as never);
    mgr.sync(specWith([ASSET]));
    await settle();
    expect(mgr.budgetUsage().triangles).toBeGreaterThan(0);

    mgr.handleContextLost();
    const statuses = sceneAssetStatusSnapshot();
    expect(statuses.some((e) => e.assetId === 'campus' && e.code === 'context_lost'
      && e.reason === 'webgl_context_lost')).toBe(true);
    // GPU 侧释放：预算记账清零（模型已 dispose）
    expect(mgr.budgetUsage().triangles).toBe(0);
    // lost 期间 sync 守卫：不启动新加载
    const s = mgr.sync(specWith([ASSET, { ...ASSET, id: 'other' }]));
    expect(s).toEqual({ mounted: 0, removed: 0, updated: 0 });
    // 幂等：重复 lost 不重复标记
    mgr.handleContextLost();
    const ctxLostEvents = sceneAssetStatusSnapshot().filter((e) => e.code === 'context_lost');
    expect(ctxLostEvents).toHaveLength(1);
    mgr.dispose();
  });

  it('context restored: limited rebuild remounts assets with status chain', async () => {
    const map = makeFakeMap();
    const mgr = new Scene3DManager(map as never);
    mgr.sync(specWith([ASSET]));
    await settle();
    const fetchCallsBefore = (globalThis.fetch as ReturnType<typeof vi.fn>).mock.calls.length;
    const addLayersBefore = map.calls.addLayer.length;

    mgr.handleContextLost();
    mgr.handleContextRestored(specWith([ASSET]));
    await settle();

    // 重挂：新的 addLayer + 重新 fetch + mounted 终态
    expect(map.calls.addLayer.length).toBeGreaterThan(addLayersBefore);
    expect((globalThis.fetch as ReturnType<typeof vi.fn>).mock.calls.length).toBeGreaterThan(fetchCallsBefore);
    const statuses = sceneAssetStatusSnapshot();
    expect(statuses.some((e) => e.code === 'context_lost')).toBe(true);
    expect(statuses.some((e) => e.code === 'mounted' && e.assetId === 'campus')).toBe(true);
    expect(mgr.describe().some((a) => a.assetId === 'campus' && a.state === 'mounted')).toBe(true);
    mgr.dispose();
  });

  it('restored without a prior lost is a no-op', async () => {
    const map = makeFakeMap();
    const mgr = new Scene3DManager(map as never);
    mgr.sync(specWith([ASSET]));
    await settle();
    const addLayersBefore = map.calls.addLayer.length;
    mgr.handleContextRestored(specWith([ASSET]));
    expect(map.calls.addLayer.length).toBe(addLayersBefore);
    mgr.dispose();
  });
});

describe('local presentation overrides (M5)', () => {
  beforeEach(() => {
    vi.stubGlobal('fetch', vi.fn(async () => {
      const buf = buildGlbFixture({ triangles: 1 });
      return new Response(buf, { status: 200, headers: { 'content-length': String(buf.byteLength) } });
    }));
  });

  it('visible/opacity overrides apply to mounted model and survive re-sync', async () => {
    const map = makeFakeMap();
    const mgr = new Scene3DManager(map as never);
    mgr.sync(specWith([ASSET]));
    await settle();
    expect(mgr.setAssetPresentation('campus', { visible: false, opacity: 0.4 })).toBe(true);
    const desc = mgr.describe().find((a) => a.assetId === 'campus');
    expect(desc?.visibleEffective).toBe(false);
    // spec 重同步（无变更）不重置表象
    mgr.sync(specWith([ASSET]));
    expect(mgr.describe().find((a) => a.assetId === 'campus')?.visibleEffective).toBe(false);
    // spec 层隐藏叠加：spec visible=false + 本地 true 仍隐藏（spec 优先收窄）
    mgr.setAssetPresentation('campus', { visible: true });
    mgr.sync(specWith([{ ...ASSET, visible: false }]));
    expect(mgr.describe().find((a) => a.assetId === 'campus')?.visibleEffective).toBe(false);
    expect(mgr.setAssetPresentation('unknown', { visible: false })).toBe(false);
    mgr.dispose();
  });
});
