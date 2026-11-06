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

/** Keep active sources pinned; only inactive values compete in the cold LRU.
 * This explicit budget bounds whole FeatureCollections while allowing a map
 * with more than24 active refs to settle instead of continually redownloading.
 */
export const MAX_ACTIVE_REF_SOURCES = 64;
const MAX_CACHE_ENTRIES = 24;
const MAX_TOMBSTONES = 32;
/** 失败墓碑 TTL（与 chart-artifact 的失败 TTL 同纪律）：确认失败在窗口
 *  内不重试（防风暴），窗口过后回到 unresolved —— 后端重注册同一 ref
 *  （修复/重放）时可恢复，不会永久卡死在 expired。 */
const FAILED_TTL_MS = 30_000;

const cache = new Map<string, unknown>();
interface RevisionEntry { revision: number | undefined }
const sourceRevisions = new Map<string, RevisionEntry>();
let activeRefs = new Set<string>();
let budgetOmitted = 0;
// 失败墓碑单独有界存放（≤ MAX_TOMBSTONES）：墓碑若混进 LRU，多层会话里
// 连续失败会驱逐存活数据 → 活跃层状态在 expired/loading 间抖动并触发
// 重拉风暴。墓碑只表达「确认失败」，不占数据缓存名额。
const tombstones = new Map<string, number>();
const inFlight = new Map<string, { task: Promise<void>; controller: AbortController }>();
const warned = new Set<string>();
let generation = 0;
const listeners = new Set<() => void>();

function cacheSet(key: string, value: unknown): void {
  if (value === FAILED) {
    cache.delete(key);
    tombstones.set(key, Date.now());
    const coldFailures = [...tombstones.keys()].filter((ref) => !activeRefs.has(ref));
    for (const ref of coldFailures.slice(0, Math.max(0, coldFailures.length - MAX_TOMBSTONES))) {
      tombstones.delete(ref);
    }
    return;
  }
  tombstones.delete(key);
  // Map 迭代序即插入序：refresh 命中先删再插 → 真 LRU。
  cache.delete(key);
  cache.set(key, value);
  trimInactive();
}

function trimInactive(): void {
  const cold = [...cache.keys()].filter((ref) => !activeRefs.has(ref));
  for (const ref of cold.slice(0, Math.max(0, cold.length - MAX_CACHE_ENTRIES))) cache.delete(ref);
  const coldFailures = [...tombstones.keys()].filter((ref) => !activeRefs.has(ref));
  for (const ref of coldFailures.slice(0, Math.max(0, coldFailures.length - MAX_TOMBSTONES))) tombstones.delete(ref);
  for (const ref of sourceRevisions.keys()) {
    if (!activeRefs.has(ref) && !cache.has(ref) && !tombstones.has(ref) && !inFlight.has(ref)) {
      sourceRevisions.delete(ref);
    }
  }
  for (const ref of warned) if (!activeRefs.has(ref)) warned.delete(ref);
}

export function getRefSourceBudget(): { limit: number; omitted: number } {
  return { limit: MAX_ACTIVE_REF_SOURCES, omitted: budgetOmitted };
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
  sourceRevisions.clear();
  tombstones.clear();
  for (const { controller } of inFlight.values()) controller.abort();
  inFlight.clear();
  activeRefs.clear();
  budgetOmitted = 0;
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

/** 用户显式重试（Layer Workspace 单层 retry）：清除失败墓碑 + 缓存条目，
 *  允许立即重拉（不等 30s TTL 自然过期）。additive（workbench-v4 Wave 2）。 */
export function clearRefSourceFailed(refId: string): void {
  if (!refId) return;
  const hadTombstone = tombstones.delete(refId);
  const hadEntry = cache.delete(refId);
  if (hadTombstone || hadEntry) emit();
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
  const requested = new Set<string>();
  for (const [sid, source] of Object.entries(spec.sources || {})) {
    if (!layerSources.has(sid) || !isRefOnlySource(source)) continue;
    const ref = refOf(source as unknown as Record<string, unknown>);
    if (ref) requested.add(ref);
  }
  activeRefs = new Set([...requested].slice(0, MAX_ACTIVE_REF_SOURCES));
  budgetOmitted = Math.max(0, requested.size - activeRefs.size);
  for (const [ref, pending] of inFlight) {
    if (!activeRefs.has(ref)) {
      inFlight.delete(ref);
      pending.controller.abort();
    }
  }
  trimInactive();
  if (layerSources.size === 0) return spec;

  const owned = hudOwnedRefs ?? new Set<string>();
  let changed = false;
  const sources: Record<string, MapSpecSource> = { ...(spec.sources || {}) };

  for (const [sid, source] of Object.entries(spec.sources || {})) {
    if (!layerSources.has(sid) || !isRefOnlySource(source)) continue;
    const s = source as unknown as Record<string, unknown>;
    const refId = refOf(s);
    if (!refId) continue;

    if (!activeRefs.has(refId)) continue;
    const revision = typeof s.content_revision === 'number' && Number.isFinite(s.content_revision)
      ? s.content_revision : undefined;
    const existing = sourceRevisions.get(refId);
    // Absence of revision evidence (including first observation) is not proof
    // of a data change. Replace the identity only on two known, unequal values.
    if (existing && existing.revision !== undefined && revision !== undefined && existing.revision !== revision) {
      cache.delete(refId);
      tombstones.delete(refId);
      inFlight.get(refId)?.controller.abort();
      inFlight.delete(refId);
      sourceRevisions.set(refId, { revision });
    } else if (!existing) {
      sourceRevisions.set(refId, { revision });
    } else if (revision !== undefined) {
      existing.revision = revision;
    }
    const hit = cache.get(refId);
    if (hit && hit !== FAILED) {
      cacheSet(refId, hit); // LRU refresh：活跃 ref 不被淘汰
      sources[sid] = { ...source, inlineData: hit } as unknown as MapSpecSource;
      changed = true;
      continue;
    }
    if (tombstoneActive(refId) || owned.has(refId)) continue;
    const profile = s.profile;
    const featureCount = profile && typeof profile === "object"
      ? (profile as Record<string, unknown>).featureCount : undefined;
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
  const fetchEntry = sourceRevisions.get(refId);
  const controller = new AbortController();
  const isCurrent = () => !controller.signal.aborted &&
    getMapSpecSessionCursor().sessionId === fetchSessionId && sourceRevisions.get(refId) === fetchEntry;
  const task = (async () => {
    try {
      const geojson = await apiFetch<{
        type: string;
        features?: unknown[];
      }>(
        `/api/v1/layers/data/${encodeURIComponent(refId)}?session_id=${encodeURIComponent(fetchContext.sessionId)}`,
        {
          ownerToken: fetchContext.ownerToken,
          signal: controller.signal,
          timeoutMs: 120_000,
          label: 'Ref source resolve error',
        },
      );
      if (!isCurrent()) return;
      if (geojson && (geojson.type === 'FeatureCollection' || Array.isArray(geojson.features))) {
        cacheSet(refId, geojson);
      } else {
        cacheSet(refId, FAILED);
      }
    } catch {
      if (isCurrent()) {
        cacheSet(refId, FAILED);
      }
    }
  })().finally(() => {
    if (inFlight.get(refId)?.task === task) inFlight.delete(refId);
    if (isCurrent()) emit();
  });
  inFlight.set(refId, { task, controller });
}
