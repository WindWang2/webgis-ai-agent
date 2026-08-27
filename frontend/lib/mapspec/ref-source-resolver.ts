import { apiFetch } from '@/lib/api/transport';
import type { MapSpec, MapSpecSource } from '@/lib/mapspec-compiler/types';
import { devOnly } from '@/lib/utils/logger';

/**
 * 通用 ref 源解析器（committed MapSpec → live runtime 的兜底数据通道）。
 *
 * 后端直写 MapSpec 的图层（webgis_map_product / webgis_layer_upsert）携带
 * `{type:"geojson", ref_id}` 源 —— live runtime 的 applySource 只认
 * inlineData/url/dataPath/tiles，ref-only 源永远挂不上地图
 * （`layers.product-*-heatmap: source "..." not found`）。
 *
 * 常规解析通道是 HUD 图层（工具事件 geojson_ref → addLayer → 异步拉取 →
 * live-spec.mergeHudSources 按 _mapspecLayerId 并入源）。两条补充：
 *  1. live-spec 同一 ref 的 HUD 数据按 ref_id 身份并入（见 mergeHudSources）；
 *  2. 本模块：仍为 ref-only 且**无 HUD 挂靠**的源，经
 *     /api/v1/layers/data/{ref_id} 拉取后注入 inlineData。拉取完成 bump
 *     generation，map-panel 的 reconcile effect 重跑，diff 引擎对源更新自动
 *     recompile 依赖图层，完成补挂载。
 *
 * 缓存按 refId 全局去重；会话切换（session-cursor.resetLiveState）清空。
 */

const FAILED = Symbol('ref-source-fetch-failed');

/** 拉取上限：超大 ref 不整包下发（HUD 大层走 MVT；这里保守放弃并告警）。 */
const FETCH_FEATURE_CAP = 20000;

const cache = new Map<string, unknown>();
const inFlight = new Map<string, Promise<void>>();
const warned = new Set<string>();
let generation = 0;
const listeners = new Set<() => void>();

function emit(): void {
  generation += 1;
  listeners.forEach((listener) => listener());
}

export function subscribeRefSources(listener: () => void): () => void {
  listeners.add(listener);
  return () => {
    listeners.delete(listener);
  };
}

export function getRefSourcesGeneration(): number {
  return generation;
}

export function resetRefSourceCache(): void {
  cache.clear();
  inFlight.clear();
  warned.clear();
}

/** ref-only geojson 源：有数据 ref，但没有任何 runtime 可直接应用的载荷。 */
export function isRefOnlySource(source: MapSpecSource | undefined | null): boolean {
  if (!source || typeof source !== 'object') return false;
  const s = source as unknown as Record<string, unknown>;
  if (s.inlineData != null || s.url != null || s.dataPath != null) return false;
  if (Array.isArray(s.tiles) && s.tiles.length > 0) return false;
  if (s.type !== 'geojson') return false;
  return typeof s.ref_id === 'string' || typeof s.ref === 'string';
}

function refOf(source: Record<string, unknown>): string | null {
  const r = source.ref_id ?? source.ref;
  return typeof r === 'string' ? r : null;
}

export interface RefFetchContext {
  sessionId: string;
  ownerToken: string | null;
}

/**
 * 把缓存命中的 ref 数据注入 spec 的源（纯投影）；未命中且无 HUD 挂靠的
 * ref 触发一次性后台拉取。返回的 spec 与入参共享未触及的对象。
 *
 * @param hudOwnedRefs HUD 图层已挂靠的数据 ref（其解析走 live-spec 的
 *   ref_id 合并，本模块不再重复拉取同一份）。
 */
export function injectResolvedRefSources(
  spec: MapSpec,
  fetchContext: RefFetchContext | null | undefined,
  hudOwnedRefs?: Set<string>,
): MapSpec {
  const layerSources = new Set((spec.layers || []).map((l) => String(l.source || '')));
  if (layerSources.size === 0) return spec;

  const owned = hudOwnedRefs ?? new Set<string>();
  let changed = false;
  const sources: Record<string, MapSpecSource> = { ...(spec.sources || {}) };

  for (const [sid, source] of Object.entries(spec.sources || {})) {
    if (!layerSources.has(sid) || !isRefOnlySource(source)) continue;
    const s = source as unknown as Record<string, unknown>;
    const refId = refOf(s);
    if (!refId) continue;

    const hit = cache.get(refId);
    if (hit && hit !== FAILED) {
      sources[sid] = { ...source, inlineData: hit } as unknown as MapSpecSource;
      changed = true;
      continue;
    }
    if (hit === FAILED || owned.has(refId)) continue;
    const featureCount = (s.profile as any)?.featureCount;
    if (typeof featureCount === 'number' && featureCount > FETCH_FEATURE_CAP) {
      if (!warned.has(refId)) {
        warned.add(refId);
        devOnly.warn(
          `[ref-source-resolver] ref ${refId} (${featureCount} features) exceeds fetch cap ${FETCH_FEATURE_CAP}; layer not mounted`,
        );
      }
      continue;
    }
    scheduleFetch(refId, fetchContext);
  }

  return changed ? { ...spec, sources } : spec;
}

function scheduleFetch(refId: string, fetchContext: RefFetchContext | null | undefined): void {
  if (inFlight.has(refId) || !fetchContext) return;
  const task = (async () => {
    try {
      const geojson = await apiFetch<{
        type: string;
        features?: unknown[];
      }>(
        `/api/v1/layers/data/${encodeURIComponent(refId)}?session_id=${encodeURIComponent(fetchContext.sessionId)}`,
        { ownerToken: fetchContext.ownerToken, label: 'Ref source resolve error' },
      );
      if (geojson && (geojson.type === 'FeatureCollection' || Array.isArray(geojson.features))) {
        cache.set(refId, geojson);
      } else {
        cache.set(refId, FAILED);
      }
    } catch {
      cache.set(refId, FAILED);
    } finally {
      inFlight.delete(refId);
      emit();
    }
  })();
  inFlight.set(refId, task);
}
