import { apiFetch } from '@/lib/api/transport';
import { getMapSpecSessionCursor } from '@/lib/mapspec/session-cursor';
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

/**
 * 会话内缓存上限（F2）：条目值为整包 FeatureCollection（每条至多
 * FETCH_FEATURE_CAP 要素）—— 无界缓存随长会话线性吃内存（数百 MB 量
 * 级）。LRU 淘汰最旧条目；in-flight 去重保证并发期不重复拉取，淘汰后
 * 的再访问走一次普通重拉（成本 = 一次网络往返，而非内存膨胀）。
 */
const MAX_CACHE_ENTRIES = 24;
const MAX_TOMBSTONES = 32;
/** 失败墓碑 TTL（与 chart-artifact 的失败 TTL 同纪律）：确认失败在窗口
 *  内不重试（防风暴），窗口过后回到 unresolved —— 后端重注册同一 ref
 *  （修复/重放）时可恢复，不会永久卡死在 expired。 */
const FAILED_TTL_MS = 30_000;

const cache = new Map<string, unknown>();
// 失败墓碑单独有界存放（≤ MAX_TOMBSTONES）：墓碑若混进 LRU，多层会话里
// 连续失败会驱逐存活数据 → 活跃层状态在 expired/loading 间抖动并触发
// 重拉风暴。墓碑只表达「确认失败」，不占数据缓存名额。
const tombstones = new Map<string, number>();
const inFlight = new Map<string, Promise<void>>();
const warned = new Set<string>();
let generation = 0;
const listeners = new Set<() => void>();

function cacheSet(key: string, value: unknown): void {
  if (value === FAILED) {
    cache.delete(key);
    tombstones.set(key, Date.now());
    while (tombstones.size > MAX_TOMBSTONES) {
      const oldest = tombstones.keys().next().value as string | undefined;
      if (oldest === undefined) break;
      tombstones.delete(oldest);
    }
    return;
  }
  tombstones.delete(key);
  // Map 迭代序即插入序：refresh 命中先删再插 → 真 LRU。
  cache.delete(key);
  cache.set(key, value);
  while (cache.size > MAX_CACHE_ENTRIES) {
    const oldest = cache.keys().next().value as string | undefined;
    if (oldest === undefined) break;
    cache.delete(oldest);
  }
}

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
  tombstones.clear();
  inFlight.clear();
  warned.clear();
}

/** Ref fetch state probe (Layer Manager V2 status derivation): the bounded
 *  client mirror of the backend artifact liveness vocabulary — `unresolved`
 *  (never fetched / evicted from tracking), `resolved`, or `failed`
 *  (definitive fetch failure ≈ expired/evicted ref). Read-only. */
/** 墓碑是否仍在有效窗口内（过期即清理并返回 false —— 允许重拉）。 */
function tombstoneActive(refId: string): boolean {
  const at = tombstones.get(refId);
  if (at === undefined) return false;
  if (Date.now() - at > FAILED_TTL_MS) {
    tombstones.delete(refId);
    return false;
  }
  return true;
}

/** HUD/SSE 挂载路径的失败回执：写入带 TTL 的失败墓碑（30s 内状态投影为
 *  expired/failed；窗口过后允许重拉 —— 真死 ref 有披露、瞬态错误可恢复）。 */
export function markRefSourceFailed(refId: string): void {
  if (!refId) return;
  cacheSet(refId, FAILED);
}

export function getRefSourceState(refId: string): 'unresolved' | 'resolved' | 'failed' {
  if (tombstoneActive(refId)) return 'failed';
  const value = cache.get(refId);
  if (value !== undefined) return 'resolved';
  return 'unresolved';
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
      cacheSet(refId, hit); // LRU refresh：活跃 ref 不被淘汰
      sources[sid] = { ...source, inlineData: hit } as unknown as MapSpecSource;
      changed = true;
      continue;
    }
    if (tombstoneActive(refId) || owned.has(refId)) continue;
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
  // #1078(G-10): 迟到完成守卫 —— 会话切换清空 cache/inFlight 后，旧会话的
  // in-flight promise 完成时不回填新会话的缓存（外会话条目永久滞留 +
  // 一次无谓的 reconcile emit）。
  const fetchSessionId = fetchContext.sessionId;
  const task = (async () => {
    try {
      const geojson = await apiFetch<{
        type: string;
        features?: unknown[];
      }>(
        `/api/v1/layers/data/${encodeURIComponent(refId)}?session_id=${encodeURIComponent(fetchContext.sessionId)}`,
        {
          ownerToken: fetchContext.ownerToken,
          timeoutMs: 120_000,
          label: 'Ref source resolve error',
        },
      );
      if (getMapSpecSessionCursor().sessionId !== fetchSessionId) return;
      if (geojson && (geojson.type === 'FeatureCollection' || Array.isArray(geojson.features))) {
        cacheSet(refId, geojson);
      } else {
        cacheSet(refId, FAILED);
      }
    } catch {
      if (getMapSpecSessionCursor().sessionId === fetchSessionId) {
        cacheSet(refId, FAILED);
      }
    } finally {
      inFlight.delete(refId);
      emit();
    }
  })();
  inFlight.set(refId, task);
}
