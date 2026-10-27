/**
 * scene3d 跨资产累计资源预算 + setStyle 泄漏修复测试（G3DV2-B M3）。
 *
 * 账本（resource-budget.ts）纯单元 + manager 集成（tiny budgetLimits 注入
 * 触发 degraded/释放恢复）+ unmountAsset 清扫路径的显式归属释放 spy。
 */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import type { MapSpec } from '@/lib/mapspec-compiler/types.generated';
import { buildGlbFixture } from './__fixtures__/glb';
import { Scene3DManager } from './asset-manager';
import { releaseLayerOwnership } from './custom-layer';
import {
  amountsOfStats,
  budgetDimensionReason,
  DEFAULT_SCENE_RESOURCE_BUDGET,
  SceneResourceBudgetLedger,
} from './resource-budget';
import { resetSceneAssetStatusForTests, sceneAssetStatusSnapshot } from './status';

vi.mock('./custom-layer', async (importOriginal) => {
  const orig = await importOriginal<typeof import('./custom-layer')>();
  return { ...orig, releaseLayerOwnership: vi.fn(orig.releaseLayerOwnership) };
});

function makeFakeMap() {
  const listeners = new Map<string, Set<() => void>>();
  const layers = new Set<string>();
  const map = {
    calls: { addLayer: [] as unknown[], removeLayer: [] as string[], on: [] as string[], off: [] as string[] },
    _layers: layers,
    addLayer: (layer: unknown) => {
      map.calls.addLayer.push(layer);
      const impl = layer as { id?: string };
      if (impl?.id) layers.add(impl.id);
    },
    removeLayer: (id: string) => {
      layers.delete(id);
      map.calls.removeLayer.push(id);
    },
    getLayer: (id: string) => (layers.has(id) ? { id } : undefined),
    on: (ev: string, h: () => void) => {
      map.calls.on.push(ev);
      if (!listeners.has(ev)) listeners.set(ev, new Set());
      listeners.get(ev)!.add(h);
    },
    off: (ev: string, h: () => void) => { map.calls.off.push(ev); listeners.get(ev)?.delete(h); },
    triggerRepaint: () => {},
    getCanvasContainer: () => ({ clientHeight: 800 }),
    getContainer: () => ({ clientHeight: 800 }),
    queryTerrainElevation: () => null,
  };
  return map;
}

const GLB_A = 'https://assets.test/campus.glb';
const GLB_B = 'https://assets.test/tower.glb';

function specWith(assets: unknown[], mode = '3d'): MapSpec {
  return {
    version: '1.7',
    sources: {
      'model-a': { type: 'model3d', format: 'glb', url: GLB_A, content_revision: 1 } as never,
      'model-b': { type: 'model3d', format: 'glb', url: GLB_B, content_revision: 1 } as never,
    },
    layers: [],
    scene: { mode, assets: assets as never } as never,
  } as MapSpec;
}

const ASSET_A = { id: 'campus', source: 'model-a', placement: { anchor: [104.06, 30.67], altitude_m: 500 }, visible: true };
const ASSET_B = { id: 'tower', source: 'model-b', placement: { anchor: [104.06, 30.67], altitude_m: 500 }, visible: true };

let fetchMock: ReturnType<typeof vi.fn>;

beforeEach(() => {
  resetSceneAssetStatusForTests();
  vi.mocked(releaseLayerOwnership).mockClear();
  fetchMock = vi.fn(async (url: string) => {
    if (url === GLB_A || url === GLB_B) {
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

describe('SceneResourceBudgetLedger (unit)', () => {
  it('tracks usage per owner and releases on dispose', () => {
    const ledger = new SceneResourceBudgetLedger({ maxTriangles: 10 });
    ledger.acquire('a\u0000single', { triangles: 6, geometryBytes: 100 });
    ledger.acquire('b\u00000/0', { triangles: 3 });
    expect(ledger.usage()).toEqual({
      triangles: 9,
      geometryBytes: 100,
      textureBytes: 0,
      cpuDecodedBytes: 0,
      materials: 0,
    });
    ledger.release('a\u0000single');
    expect(ledger.usage().triangles).toBe(3);
    ledger.release('unknown'); // 幂等
    expect(ledger.usage().triangles).toBe(3);
  });

  it('wouldExceed reports the over-limit dimension', () => {
    const ledger = new SceneResourceBudgetLedger({ maxTriangles: 5 });
    ledger.acquire('x', { triangles: 4 });
    expect(ledger.wouldExceed({ triangles: 1 })).toBeNull();
    expect(ledger.wouldExceed({ triangles: 2 })).toBe('maxTriangles');
    expect(budgetDimensionReason('maxTriangles')).toBe('budget_exceeded:triangles');
  });

  it('custom limits only override provided dimensions', () => {
    const ledger = new SceneResourceBudgetLedger({ maxMaterials: 2 });
    expect(ledger.limitsView().maxMaterials).toBe(2);
    expect(ledger.limitsView().maxGeometryBytes).toBe(DEFAULT_SCENE_RESOURCE_BUDGET.maxGeometryBytes);
  });

  it('amountsOfStats maps parser stats to ledger amounts', () => {
    expect(amountsOfStats({ triangles: 7, geometryBytes: 128, textureBytes: 64, materialCount: 2 })).toEqual({
      triangles: 7,
      geometryBytes: 128,
      textureBytes: 64,
      cpuDecodedBytes: 128,
      materials: 2,
    });
  });
});

describe('Scene3DManager cumulative budget (integration)', () => {
  it('degrades second asset with typed budget_exceeded and keeps first mounted', async () => {
    const map = makeFakeMap();
    // 每个 fixture GLB = 2 三角；上限 3 → 第二个资产必超（2+2 > 3）。
    const mgr = new Scene3DManager(map as never, { budgetLimits: { maxTriangles: 3 } });
    mgr.sync(specWith([ASSET_A]));
    await settle();
    expect(mgr.describe().some((a) => a.assetId === 'campus' && a.state === 'mounted')).toBe(true);
    expect(mgr.budgetUsage().triangles).toBe(2);

    mgr.sync(specWith([ASSET_A, ASSET_B]));
    await settle();
    const statuses = sceneAssetStatusSnapshot();
    expect(statuses.some((e) => e.assetId === 'tower' && e.code === 'degraded'
      && e.reason === 'budget_exceeded:triangles')).toBe(true);
    // 首资产不受影响；tower 未被装下
    expect(mgr.describe().some((a) => a.assetId === 'campus' && a.state === 'mounted')).toBe(true);
    expect(mgr.describe().some((a) => a.assetId === 'tower')).toBe(false);
    expect(mgr.budgetUsage().triangles).toBe(2);
    mgr.dispose();
  });

  it('budget is released on unmount: removing first asset lets the second mount', async () => {
    const map = makeFakeMap();
    const mgr = new Scene3DManager(map as never, { budgetLimits: { maxTriangles: 3 } });
    mgr.sync(specWith([ASSET_A]));
    await settle();
    // 换成只有 tower（campus 卸载 → 记账归还 → tower 在预算内）
    mgr.sync(specWith([ASSET_B]));
    await settle();
    expect(mgr.describe().some((a) => a.assetId === 'tower' && a.state === 'mounted')).toBe(true);
    expect(mgr.budgetUsage().triangles).toBe(2);
    mgr.dispose();
    expect(mgr.budgetUsage().triangles).toBe(0);
  });
});

describe('setStyle sweep ownership release (G5 leak fix)', () => {
  it('unmount of a swept layer explicitly releases shared ownership', async () => {
    const map = makeFakeMap();
    const mgr = new Scene3DManager(map as never);
    mgr.sync(specWith([ASSET_A]));
    await settle();
    // 模拟 basemap setStyle：层被抹除且无 onRemove 回调。
    map._layers.clear();
    mgr.sync(specWith([]));
    expect(releaseLayerOwnership).toHaveBeenCalledTimes(1);
    mgr.dispose();
  });

  it('normal removal (layer present) goes through removeLayer without manual release', async () => {
    const map = makeFakeMap();
    const mgr = new Scene3DManager(map as never);
    mgr.sync(specWith([ASSET_A]));
    await settle();
    mgr.sync(specWith([]));
    expect(releaseLayerOwnership).not.toHaveBeenCalled();
    expect(map.calls.removeLayer).toContain('scene3d-campus');
    mgr.dispose();
  });

  it('repeated setStyle cycles do not accumulate ownership (remount after sweep)', async () => {
    const map = makeFakeMap();
    const mgr = new Scene3DManager(map as never);
    for (let i = 0; i < 3; i++) {
      mgr.sync(specWith([ASSET_A]));
      await settle();
      map._layers.clear(); // setStyle sweep
      mgr.sync(specWith([ASSET_A])); // 强制重挂（getLayer 缺席）
      await settle();
      map._layers.clear();
      mgr.sync(specWith([])); // 清扫路径卸载 → 显式释放
    }
    // 每轮 2 次清扫卸载（强制重挂的 unmount + 最终 sync([]) 移除），每次
    // 释放一份归属 —— 与重挂时的 acquire 一一配对，不随 setStyle 次数净增。
    expect(releaseLayerOwnership).toHaveBeenCalledTimes(6);
    mgr.dispose();
  });
});
