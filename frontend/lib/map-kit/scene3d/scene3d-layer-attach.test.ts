/**
 * scene3d custom layer 挂载失败路径（G3DV2-B）：addLayer 抛错时层从未挂上、
 * onAdd 也从未 acquireRenderer —— 卸载不得走"层缺失"分支释放共享归属
 * （否则共享 carrier refCount 被无偿 -1，同图首资产的 WebGLRenderer 被提前
 * dispose）。
 */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import type { MapSpec } from '@/lib/mapspec-compiler/types.generated';
import { buildGlbFixture } from './__fixtures__/glb';
import { Scene3DManager } from './asset-manager';
import { getCarrier, releaseLayerOwnership } from './custom-layer';
import { resetSceneAssetStatusForTests, sceneAssetStatusSnapshot } from './status';

// three 的 WebGLRenderer 在 jsdom 无 WebGL context —— 只替身渲染器本体，
// 其余 THREE 语义保持真实（acquireRenderer 的 carrier 记账因此可观测）。
vi.mock('three', async (importOriginal) => {
  const orig = await importOriginal<typeof import('three')>();
  class FakeWebGLRenderer {
    autoClear = true;
    outputColorSpace = '';
    disposed = false;
    constructor(_params?: unknown) {}
    resetState() {}
    render() {}
    dispose() { this.disposed = true; }
  }
  return { ...orig, WebGLRenderer: FakeWebGLRenderer };
});

// 与 scene3d-budget.test.ts 同款 spy：保留原实现，只计数。
vi.mock('./custom-layer', async (importOriginal) => {
  const orig = await importOriginal<typeof import('./custom-layer')>();
  return { ...orig, releaseLayerOwnership: vi.fn(orig.releaseLayerOwnership) };
});

const GLB_A = 'https://assets.test/campus.glb';
const GLB_B = 'https://assets.test/tower.glb';

function specWith(assets: unknown[]): MapSpec {
  return {
    version: '1.7',
    sources: {
      'model-a': { type: 'model3d', format: 'glb', url: GLB_A, content_revision: 1 } as never,
      'model-b': { type: 'model3d', format: 'glb', url: GLB_B, content_revision: 1 } as never,
    },
    layers: [],
    scene: { mode: '3d', assets: assets as never } as never,
  } as MapSpec;
}

const ASSET_A = { id: 'campus', source: 'model-a', placement: { anchor: [104.06, 30.67], altitude_m: 500 }, visible: true };
const ASSET_BAD = { id: 'bad', source: 'model-b', placement: { anchor: [104.06, 30.67], altitude_m: 500 }, visible: true };

interface FakeLayer {
  id: string;
  onAdd?: (m: unknown, g: unknown) => void;
  onRemove?: (m: unknown, g: unknown) => void;
}

/**
 * fake map：addLayer/removeLayer 同步回调 onAdd/onRemove（MapLibre 语义），
 * 可按层 id 抛错；`sweep()` 模拟 setStyle 抹除全部层且不回调 onRemove。
 */
function makeFakeMap(rejectLayerId: string | null) {
  const layers = new Map<string, FakeLayer>();
  const gl = { canvas: {} } as never;
  const map = {
    layers,
    addLayer: (layer: unknown) => {
      const impl = layer as FakeLayer;
      if (impl?.id === rejectLayerId) throw new Error('addLayer rejected');
      if (impl?.id) layers.set(impl.id, impl);
      impl.onAdd?.(map, gl);
    },
    removeLayer: (id: string) => {
      const impl = layers.get(id);
      layers.delete(id);
      impl?.onRemove?.(map, gl);
    },
    getLayer: (id: string) => (layers.has(id) ? { id } : undefined),
    sweep: () => layers.clear(),
    on: () => {},
    off: () => {},
    triggerRepaint: () => {},
    getCanvasContainer: () => ({ clientHeight: 800 }),
    getContainer: () => ({ clientHeight: 800 }),
    queryTerrainElevation: () => null,
  };
  return map;
}

beforeEach(() => {
  resetSceneAssetStatusForTests();
  vi.mocked(releaseLayerOwnership).mockClear();
  vi.stubGlobal('fetch', vi.fn(async (url: string) => {
    if (url === GLB_A || url === GLB_B) {
      const buf = buildGlbFixture({ triangles: 2 });
      return new Response(buf, { status: 200, headers: { 'content-length': String(buf.byteLength) } });
    }
    return new Response('not found', { status: 404 });
  }));
});

afterEach(() => {
  vi.unstubAllGlobals();
  resetSceneAssetStatusForTests();
});

async function settle(ms = 20): Promise<void> {
  await new Promise((r) => setTimeout(r, ms));
}

describe('custom layer 挂载失败（addLayer 抛错）', () => {
  it('失败资产不卸载共享 carrier 归属：首资产 refCount 不被无偿 -1', async () => {
    const map = makeFakeMap('scene3d-bad');
    const mgr = new Scene3DManager(map as never);
    mgr.sync(specWith([ASSET_A, ASSET_BAD]));
    await settle();

    // 失败资产未挂载、typed unavailable 记录
    expect(mgr.describe().some((a) => a.assetId === 'bad')).toBe(false);
    expect(mgr.describe().some((a) => a.assetId === 'campus')).toBe(true);
    expect(sceneAssetStatusSnapshot().some((e) => e.assetId === 'bad'
      && e.code === 'unavailable' && e.reason === 'custom_layer_rejected')).toBe(true);

    // 共享 carrier 仍只归首资产（旧实现：unmountAsset 走"层缺失"分支调
    // releaseLayerOwnership → refCount 0 → carrier 提前 dispose/删除）。
    const carrier = getCarrier(map);
    expect(carrier).toBeDefined();
    expect(carrier!.refCount).toBe(1);
    expect(releaseLayerOwnership).not.toHaveBeenCalled();
    mgr.dispose();
    // dispose 走 removeLayer → onRemove 正常归还，不做多余显式释放
    expect(releaseLayerOwnership).not.toHaveBeenCalled();
  });

  it('setStyle 清扫路径（层曾挂上后被抹除）仍显式释放归属（不回归）', async () => {
    const map = makeFakeMap(null);
    const mgr = new Scene3DManager(map as never);
    mgr.sync(specWith([ASSET_A]));
    await settle();
    expect(getCarrier(map)?.refCount).toBe(1);
    map.sweep(); // setStyle：层被抹除且无 onRemove 回调
    mgr.sync(specWith([]));
    expect(releaseLayerOwnership).toHaveBeenCalledTimes(1);
    mgr.dispose();
  });
});
