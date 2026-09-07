/**
 * Workbench V4（Wave 2）：Layer Workspace golden-model parity 测试。
 *
 * 参考模型（ReferenceWorkspace：普通 dict + seq 应用命令/服务端文档）与
 * 真实管线（useHudStore + session-cursor pending/removed + user-mutation
 * 串行链 + composeLiveMapSpec）对同一随机命令序列独立推进，断言三不变式：
 *   I1. 每层的有效可见性（store.visible ⊕ pending）== compose(spec) 可见性
 *   I2. 已删层的 spec 层族被 compose 过滤（不复活）
 *   I3. 收敛后（无 pending）store presentation == 服务端 committed 文档
 *
 * 审计 02 的 B1/B2/B4 类漂移在此模型下可复现 —— 本文件是回归防线。
 */
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { useHudStore } from '@/lib/store/useHudStore';
import {
  commitMapSpecDocument,
  getCommittedMapSpec,
  getPendingPresentation,
  getPendingRemoved,
  setMapSpecSessionCursor,
} from '@/lib/mapspec/session-cursor';
import {
  removeLayerAndCommit,
  reorderLayersAndCommit,
  setLayerOpacityAndCommit,
  toggleLayerAndCommit,
} from '@/lib/mapspec/user-mutation';
import { composeLiveMapSpec } from '@/lib/mapspec/live-spec';
import type { MapSpec } from '@/lib/mapspec-compiler/types';
import type { Layer } from '@/lib/types/layer';

vi.mock('@/lib/api/config', () => ({ API_BASE: 'http://localhost:8000' }));

/** 确定性伪随机（可复现失败序列）。 */
function mulberry32(seed: number): () => number {
  let a = seed;
  return () => {
    a |= 0; a = (a + 0x6d2b79f5) | 0;
    let t = Math.imul(a ^ (a >>> 15), 1 | a);
    t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t;
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}

interface RefRow { id: string; visible: boolean; opacity: number; removed: boolean }

/** 参考模型：命令语义与 user-mutation 的收敛终态一致（乐观态只影响中间帧）。 */
class ReferenceWorkspace {
  rows: RefRow[] = [];
  constructor(ids: string[]) {
    this.rows = ids.map((id) => ({ id, visible: true, opacity: 1, removed: false }));
  }
  toggle(id: string): void {
    const row = this.rows.find((r) => r.id === id);
    if (row) row.visible = !row.visible;
  }
  opacity(id: string, value: number): void {
    const row = this.rows.find((r) => r.id === id);
    if (row) row.opacity = value;
  }
  remove(id: string): void {
    const row = this.rows.find((r) => r.id === id);
    if (row) row.removed = true;
  }
  /** 服务端最终文档：模型行 → spec 层（visibility/opacity）。 */
  serverDoc(): MapSpec {
    return {
      version: '1.0',
      sources: Object.fromEntries(
        this.rows.filter((r) => !r.removed).map((r) => [r.id, { type: 'geojson' }]),
      ),
      layers: this.rows.filter((r) => !r.removed).map((r) => ({
        id: r.id,
        source: r.id,
        type: 'circle' as const,
        layout: { visibility: r.visible ? 'visible' : 'none' },
        paint: { 'circle-opacity': r.opacity },
      })),
    };
  }
}

/** 构造回执 fetch：任何 mutation 返回「模型生成的服务端文档 + 新 revision」。
 *  reorder 意图按 body.layer_ids 重排模型行 —— 服务端语义（spec 层序 =
 *  提交序）必须在 echo 文档中反映，否则不变式比较失真。 */
function makeEchoingFetch(model: ReferenceWorkspace, revision: { value: number }): ReturnType<typeof vi.fn> {
  return vi.fn(async (_url: string, init?: { body?: string }) => {
    const body = JSON.parse(init?.body ?? '{}') as { intent?: string; opacity?: number; layer_ids?: string[] };
    if (body.intent === 'reorder_layers' && Array.isArray(body.layer_ids)) {
      const order = new Map(body.layer_ids.map((id, idx) => [id, idx]));
      model.rows = [...model.rows].sort(
        (a, b) => (order.get(a.id) ?? 0) - (order.get(b.id) ?? 0),
      );
    }
    revision.value += 1;
    return {
      ok: true,
      status: 200,
      statusText: 'OK',
      text: () => Promise.resolve(JSON.stringify({
        success: true,
        mutation_revision: revision.value,
        mapspec: model.serverDoc(),
      })),
    };
  });
}

function hudToSpecInput(layers: Layer[]) {
  return {
    layers,
    processLayers: {},
    activeFilters: {},
    selectionFilters: {},
    is3D: false,
  };
}

/** I1：store.visible ⊕ pending == compose 输出的每层 visibility（按行族匹配）。 */
function assertVisibilityParity(committed: MapSpec | null): void {
  const hud = useHudStore.getState();
  const pending = getPendingPresentation();
  const removed = getPendingRemoved();
  const composed = composeLiveMapSpec(
    committed,
    hudToSpecInput(hud.layers),
    pending,
    removed,
  );
  const composedVisibility = new Map<string, boolean>();
  for (const layer of composed.layers ?? []) {
    const baseId = String(layer.id || '').split('__')[0];
    const withoutCustom = baseId.startsWith('custom-') ? baseId.slice(7) : baseId;
    composedVisibility.set(String(layer.id || ''), layer.layout?.visibility !== 'none');
    composedVisibility.set(baseId, layer.layout?.visibility !== 'none');
    composedVisibility.set(withoutCustom, layer.layout?.visibility !== 'none');
  }
  for (const row of hud.layers) {
    const keys = [row.id, row._mapspecLayerId].filter(Boolean) as string[];
    for (const key of keys) {
      const pendingPatch = pending[key] ?? pending[row.id];
      const expectedVisible = pendingPatch?.visible !== undefined
        ? pendingPatch.visible
        : row.visible;
      if (composedVisibility.has(key)) {
        expect(
          composedVisibility.get(key),
          `visibility parity for ${key} (row ${row.id})`,
        ).toBe(expectedVisible);
      }
    }
  }
  // I2：已删行不在 compose 输出。
  const composedIds = new Set((composed.layers ?? []).map((l) => String(l.id || '').split('__')[0]));
  for (const removedId of removed) {
    const base = removedId.startsWith('custom-') ? removedId.slice(7) : removedId;
    expect(composedIds.has(removedId) || composedIds.has(base), `removed ${removedId} must not recompose`).toBe(false);
  }
}

beforeEach(async () => {
  vi.stubGlobal('fetch', vi.fn());
  await new Promise((resolve) => setTimeout(resolve, 0));
  useHudStore.getState().clearLayers();
  setMapSpecSessionCursor('sid-parity', 1);
  useHudStore.setState({
    layerGroups: [],
    layerGroupMembership: {},
    lockedLayerIds: [],
    selectedLayerIds: [],
    isolatedLayerId: null,
    isolatedFrom: null,
  });
});

describe('workspace parity（golden model × 随机命令序列）', () => {
  it('30 步随机 toggle/opacity/remove 序列后三不变式成立', async () => {
    const ids = ['pa', 'pb', 'pc', 'pd'];
    const model = new ReferenceWorkspace(ids);
    for (const id of ids) {
      useHudStore.getState().addLayer({
        id,
        name: id,
        type: 'vector',
        visible: true,
        opacity: 1,
        group: 'analysis',
        source: { type: 'FeatureCollection', features: [] } as any,
        _mapspecLayerId: id,
      } as any);
    }
    // 初始 committed 文档 = 模型初态。
    const revision = { value: 1 };
    const fetchMock = makeEchoingFetch(model, revision);
    vi.stubGlobal('fetch', fetchMock);
    commitMapSpecDocument(model.serverDoc(), 1);
    let committed = getCommittedMapSpec();
    assertVisibilityParity(committed);

    const rand = mulberry32(20260907);
    for (let step = 0; step < 30; step += 1) {
      const alive = model.rows.filter((r) => !r.removed);
      if (alive.length === 0) break;
      const row = alive[Math.floor(rand() * alive.length) % alive.length];
      const roll = rand();
      if (roll < 0.5) {
        model.toggle(row.id);
        await toggleLayerAndCommit(row.id);
      } else if (roll < 0.85) {
        const value = Math.round(rand() * 10) / 10;
        model.opacity(row.id, value);
        await setLayerOpacityAndCommit(row.id, value);
      } else if (alive.length > 1) {
        model.remove(row.id);
        await removeLayerAndCommit(row.id);
      }
      committed = getCommittedMapSpec();
      assertVisibilityParity(committed);
      // I3：无在途 pending（echo 串行链下每步收敛）。
      expect(getPendingPresentation()).toEqual({});
      // store 终态 == 模型终态（对未删行）。
      for (const refRow of model.rows) {
        const storeRow = useHudStore.getState().layers.find((l) => l.id === refRow.id);
        if (refRow.removed) {
          expect(storeRow, `${refRow.id} removed from store`).toBeUndefined();
          continue;
        }
        expect(storeRow).toBeDefined();
        expect(storeRow!.visible).toBe(refRow.visible);
        expect(storeRow!.opacity).toBeCloseTo(refRow.opacity, 8);
      }
    }
  });

  it('随机 reorder 序列：store 序 == committed spec 序（z-order 唯一真相）', async () => {
    const ids = ['ra', 'rb', 'rc'];
    const model = new ReferenceWorkspace(ids);
    for (const id of ids) {
      useHudStore.getState().addLayer({
        id, name: id, type: 'vector', visible: true, opacity: 1,
        source: { type: 'FeatureCollection', features: [] } as any,
        _mapspecLayerId: id,
      } as any);
    }
    const revision = { value: 1 };
    vi.stubGlobal('fetch', makeEchoingFetch(model, revision));
    commitMapSpecDocument(model.serverDoc(), 1);

    const rand = mulberry32(42);
    for (let step = 0; step < 10; step += 1) {
      const shuffled = [...useHudStore.getState().layers];
      for (let i = shuffled.length - 1; i > 0; i -= 1) {
        const j = Math.floor(rand() * (i + 1));
        [shuffled[i], shuffled[j]] = [shuffled[j], shuffled[i]];
      }
      await reorderLayersAndCommit(shuffled);
      const storeOrder = useHudStore.getState().layers.map((l) => l._mapspecLayerId ?? l.id);
      const specOrder = (getCommittedMapSpec()?.layers ?? []).map((l) => String(l.id));
      expect(storeOrder).toEqual(specOrder);
    }
  });
});
