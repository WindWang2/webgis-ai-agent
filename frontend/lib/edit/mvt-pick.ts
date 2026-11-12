/**
 * mvt-pick — MVT/大图层编辑手势的权威拾取（G24 / P2）。
 *
 * 纪律：
 * - queryRenderedFeatures 只产生**候选稳定 id**（渲染面事实），瓦片几何
 *   （裁剪/简化后）绝不直接作为可编辑对象提交 —— 权威对象一律经
 *   /layers/data/{ref}/feature/{id} 服务端回查（完整几何+属性）；
 * - 候选去重：同要素跨子层（fill+outline）/跨瓦片/多世界副本/AM 分裂件
 *   同 id 只取一次（MapLibre field-1 位置 id 场景在无链内 id 数据集，
 *   同样按 id 去重）；
 * - 精确命中：对回查到的**原始几何**在当前视口做 preciseFeatureHit
 *   （长线段中段 A09 / Polygon 洞 A08 / Multi* A10）；
 * - 取消：调用方传 AbortSignal（工具切换/目标切换/卸载即取消）；
 *   AbortError 与 404 一律诚实返回 null，不虚构命中。
 */
import type { Map as MaplibreMap, MapGeoJSONFeature } from 'maplibre-gl';
import { apiFetch } from '@/lib/api/transport';
import { featureIdFromProperties } from '@/lib/edit/layer-edit-bridge';
import { preciseFeatureHit } from '@/lib/edit/geometry-hit';
import { getSessionIdentity } from '@/lib/store/session-identity';

/** 单击候选上限（与 PoiPanel 同界；候选只用于挑 id，不承载几何）。 */
export const MAX_PICK_CANDIDATES = 5;

export interface PickCandidate {
  id: string;
  /** 命中的 MapLibre 子层 id（诊断/多候选排序披露）。 */
  sourceLayer?: string;
}

/** 渲染面候选 id（去重、截断到 MAX_PICK_CANDIDATES）。纯投影函数（可单测）。 */
export function candidateIdsFromHits(
  hits: MapGeoJSONFeature[] | Array<{ id?: number | string; properties?: Record<string, unknown> | null; layer?: { id?: string } }>,
): PickCandidate[] {
  const out: PickCandidate[] = [];
  const seen = new Set<string>();
  for (const hit of hits) {
    const id = featureIdFromProperties(
      (hit.properties ?? null) as Record<string, unknown> | null,
      (hit as { id?: unknown }).id,
    );
    if (id == null || seen.has(id)) continue;
    seen.add(id);
    out.push({ id, sourceLayer: hit.layer?.id });
    if (out.length >= MAX_PICK_CANDIDATES) break;
  }
  return out;
}

export interface AuthoritativeFetchOptions {
  sessionId: string;
  refId: string;
  featureId: string;
  /** 位置 id 回退的 revision guard（descriptor.content_revision）。 */
  revision?: number | null;
  ownerToken?: string | null;
  signal?: AbortSignal;
}

/**
 * 单要素权威回查（完整几何 + 属性）。
 * 链内 id 404 且为纯数字 → id_mode=positional 重试一次（带 v guard，
 * 与 layer-data selection-detail 同语义）；仍失败 → null（诚实）。
 */
export async function fetchAuthoritativeFeature(
  opts: AuthoritativeFetchOptions,
): Promise<GeoJSON.Feature | null> {
  const { sessionId, refId, featureId, revision, ownerToken, signal } = opts;
  const base = `/api/v1/layers/data/${encodeURIComponent(refId)}/feature/${encodeURIComponent(featureId)}`;
  const q = `?session_id=${encodeURIComponent(sessionId)}`;
  const common = { ownerToken: ownerToken ?? undefined, signal };
  try {
    return await apiFetch<GeoJSON.Feature>(base + q, { ...common, label: 'MVT pick detail error' });
  } catch (err) {
    const status = (err as { status?: number } | null)?.status;
    if (status !== 404 || !/^\d+$/.test(featureId)) return null;
  }
  // positional 重试（仅无链内 id 的数据集；revision 内稳定 —— 不带 v 的
  // 服务端仍会以 descriptor 快照核对，带上客户端已知 revision 更严）
  const revQ = typeof revision === 'number' ? `&v=${revision}` : '';
  try {
    return await apiFetch<GeoJSON.Feature>(
      `${base + q}${revQ}&id_mode=positional`,
      { ...common, label: 'MVT pick detail (positional) error' },
    );
  } catch {
    return null;
  }
}

export interface AuthoritativePickOptions {
  map: MaplibreMap;
  point: { x: number; y: number };
  lngLat: [number, number];
  /** 父图层 id（交互子层按 `${layerId}__` 前缀过滤）。 */
  layerId: string;
  refId: string;
  /** 覆盖会话身份（缺省取 session-identity）。 */
  sessionId?: string;
  revision?: number | null;
  tolerancePx?: number;
  signal?: AbortSignal;
}

export interface AuthoritativePick {
  id: string;
  feature: GeoJSON.Feature;
  distPx: number;
}

/**
 * 候选 → 权威回查 → 精确命中（多候选取命中距离最近者，可解释）。
 * 无稳定 id 候选 / 回查全失败 / 全部未命中 → null。
 */
export async function pickAuthoritativeFeature(
  opts: AuthoritativePickOptions,
): Promise<AuthoritativePick | null> {
  const { map, point, lngLat, layerId, refId, tolerancePx = 6 } = opts;
  const sessionId = opts.sessionId ?? getSessionIdentity().sessionId;
  if (!sessionId) return null;
  let hits: MapGeoJSONFeature[] = [];
  try {
    hits = map.queryRenderedFeatures([point.x, point.y] as [number, number], {
      layers: map.getStyle().layers
        .map((l) => l.id)
        .filter((id): id is string => !!id && id.startsWith(`${layerId}__`)),
    });
  } catch {
    return null; // 会话切换/层失效瞬间 —— 静默收敛
  }
  const candidates = candidateIdsFromHits(hits);
  if (!candidates.length) return null;
  const { ownerToken } = getSessionIdentity();
  let best: AuthoritativePick | null = null;
  for (const cand of candidates) {
    if (opts.signal?.aborted) return null;
    const feature = await fetchAuthoritativeFeature({
      sessionId, refId, featureId: cand.id, revision: opts.revision,
      ownerToken, signal: opts.signal,
    });
    if (!feature || !feature.geometry) continue;
    const result = preciseFeatureHit(feature, lngLat, (p) => map.project(p), point, tolerancePx);
    if (!result.hit) continue;
    if (!best || result.distPx < best.distPx) {
      best = { id: cand.id, feature, distPx: result.distPx };
    }
  }
  return best;
}
