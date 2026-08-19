/**
 * Session map-state restore — 会话恢复 / 分享页（/story）共用的图层还原逻辑。
 *
 * #552 之前该逻辑只存在于 use-workspace-session 的 selectSession 内部，/story
 * 分享页「地图永远空白」：页面只拉 messages，从不恢复 map-state。抽成单一事实
 * 来源后，两条路径（主应用会话切换 / 分享回放页）渲染同一份最终地图快照。
 *
 * 语义（与 selectSession 原实现逐字段一致）：
 *   - post-reconcile 观察态（_cartographic_observation）是指纹匹配时的最终
 *     快照，优先于 turn-start 的 `layers`；
 *   - 旧会话无运行态证据时回退持久化 `layers`；
 *   - ref 图层：MVT-capable 且超阈值 → 由瓦片端点显示（_tileUrl），否则整包
 *     GeoJSON 拉取回填（SEC-08：匿名会话带 ownerToken）。
 */
import { apiFetch } from '@/lib/api/transport';
import { API_BASE } from '@/lib/api/config';
import type { GeoJSONFeatureCollection, MapActionPayload } from '@/lib/types';
import { useHudStore } from '@/lib/store/useHudStore';
import { useToastStore } from '@/components/ui/toast';
import { devOnly } from '@/lib/utils/logger';
import { commitMapSpecDocument } from '@/lib/mapspec/session-cursor';

function isAbortError(err: unknown): boolean {
  return (err instanceof DOMException && err.name === 'AbortError')
    || (err instanceof Error && err.name === 'AbortError');
}

export function reportLayerFetchFailure(context: string, layerLabel: string, err: unknown): void {
  if (isAbortError(err)) return;
  devOnly.error(context, err);
  useToastStore.getState().addToast(
    `图层「${layerLabel}」数据加载失败，地图上可能显示为空。请稍后重试或刷新会话`,
    'error',
  );
}

/** 持久化 map-state 中 restore 消费的形状。 */
export interface SessionMapState {
  base_layer?: string | null;
  viewport?: {
    center?: [number, number];
    zoom?: number;
    bearing?: number;
    pitch?: number;
  } | null;
  layers?: any[];
  mapspec?: {
    layers?: any[];
    view?: {
      center?: number[];
      zoom?: number;
      bearing?: number;
      pitch?: number;
      framed?: boolean;
    };
  };
  _cartographic_mutation_revision?: number;
  _current_cartographic_fingerprint?: string;
  _cartographic_observation?: { mapspec_fingerprint?: string; layers?: any[] };
}

/** 观察态是否为当前代次（指纹匹配才算数，旧代次观察不得覆盖权威 layers）。 */
function observationIsCurrent(state: SessionMapState): boolean {
  const observation = state._cartographic_observation;
  return (
    typeof state._current_cartographic_fingerprint === 'string'
    && typeof observation?.mapspec_fingerprint === 'string'
    && observation.mapspec_fingerprint === state._current_cartographic_fingerprint
  );
}

/**
 * 挑选要恢复的图层：指纹匹配的观察态（最终快照）优先；否则回退持久化 layers。
 */
export function selectLayersToRestore(state: SessionMapState): any[] {
  const observation = state._cartographic_observation;
  const observedLayers = observationIsCurrent(state) && Array.isArray(observation?.layers)
    ? observation.layers
    : [];
  return observedLayers.length > 0 ? observedLayers : (state.layers || []);
}

/** 单条观察图层 → HUD Layer（字段映射与 selectSession 原实现一致）。 */
/** Desired camera only when MapSpec.view was an explicit frame (ADR-0057). */
export function selectCameraToRestore(
  state: SessionMapState,
): {
  center: [number, number];
  zoom?: number;
  bearing?: number;
  pitch?: number;
} | null {
  const view = state.mapspec?.view;
  if (!view || view.framed !== true) return null;
  const center = view.center;
  if (!Array.isArray(center) || center.length < 2) return null;
  if (typeof center[0] !== 'number' || typeof center[1] !== 'number') return null;
  return {
    center: [center[0], center[1]],
    zoom: typeof view.zoom === 'number' ? view.zoom : undefined,
    bearing: typeof view.bearing === 'number' ? view.bearing : undefined,
    pitch: typeof view.pitch === 'number' ? view.pitch : undefined,
  };
}

export function presentationFromMapSpec(
  mapspec: { layers?: any[] } | undefined,
  layerId: string,
): { visible?: boolean; opacity?: number } {
  const layers = mapspec?.layers;
  if (!Array.isArray(layers) || !layerId) return {};
  const match = layers.find((layer) => {
    const id = String(layer?.id || '');
    return id === layerId || id.startsWith(`${layerId}__`);
  });
  if (!match) return {};
  const next: { visible?: boolean; opacity?: number } = {};
  const visibility = match.layout?.visibility;
  if (visibility === 'none') next.visible = false;
  if (visibility === 'visible') next.visible = true;
  const paint = match.paint && typeof match.paint === 'object' ? match.paint : {};
  const opacity = paint.opacity ?? paint['circle-opacity'] ?? paint['fill-opacity']
    ?? paint['line-opacity'] ?? paint['raster-opacity'] ?? paint['heatmap-opacity'];
  if (typeof opacity === 'number') next.opacity = opacity;
  return next;
}

export function buildLayerFromRestored(
  observed: any,
  sessionId: string,
  mapspecFingerprint?: string,
  mapspec?: { layers?: any[] },
) {
  const refId = observed._refId;
  const runtimeId = observed.runtime_store_id ?? refId ?? observed.id;
  const rasterSource = (
    typeof observed.raster_image === 'string'
    && Array.isArray(observed.raster_bbox)
    && observed.raster_bbox.length === 4
  ) ? {
      image: observed.raster_image,
      bbox: observed.raster_bbox,
    } : null;
  const base = {
    id: runtimeId,
    name: observed.name ?? `分析结果: ${observed.id}`,
    type: rasterSource
      ? 'heatmap'
      : ['vector', 'raster', 'tile', 'heatmap'].includes(observed.type)
        ? observed.type
        : 'vector',
    visible: observed.visible !== false,
    opacity: typeof observed.opacity === 'number' ? observed.opacity : 1,
    group: observed.group ?? 'analysis',
    source: rasterSource ?? ({
      type: 'FeatureCollection',
      features: [],
      metadata: { ref_id: refId },
    } as GeoJSONFeatureCollection),
    style: observed.style,
    legend_spec: observed.legend_spec,
    _refId: refId,
    _descriptor: observed._descriptor,
    _tileUrl: refId
      ? `${API_BASE}/api/v1/layers/data/${refId}/tiles/{z}/{x}/{y}.mvt?session_id=${sessionId}`
      : undefined,
    _mapspecFingerprint: mapspecFingerprint,
    _mapspecLayerId: observed.id,
    _mapspecProjectionFingerprint: observed.projection_fingerprint,
    _mapspecRepairActionId: observed.repair_action_id,
    _intentGeneration: typeof observed.intent_generation === 'number'
      ? observed.intent_generation
      : undefined,
  };
  return { ...base, ...presentationFromMapSpec(mapspec, String(observed.id ?? runtimeId)) };
}

export interface RestoreMapLayersOptions {
  sessionId: string;
  /** SEC-08：匿名会话的图层引用数据同样受 owner_token 保护。 */
  token?: string | null;
  signal?: AbortSignal;
}

/** 把会话 map-state 的图层部分应用到 HUD store（含 ref 数据回填）。 */
export async function restoreSessionMapLayers(
  state: SessionMapState,
  opts: RestoreMapLayersOptions,
): Promise<void> {
  commitMapSpecDocument(state.mapspec);
  const store = useHudStore.getState();
  const raw = selectLayersToRestore(state);
  const observation = state._cartographic_observation;
  const fromObservation =
    observationIsCurrent(state)
    && Array.isArray(observation?.layers)
    && raw.length > 0;
  // The live post-reconcile observation is the final-map snapshot. It outranks
  // the turn-start `layers` state, which may predate the GIS result. Legacy
  // sessions without runtime evidence keep the old path (raw persisted layers).
  const allowedIds = new Set(
    (state.mapspec?.layers || [])
      .map((layer: any) => String(layer?.id || ''))
      .flatMap((id: string) => (id.includes('__') ? [id, id.split('__')[0]] : [id]))
      .filter(Boolean),
  );

  const layersToRestore = fromObservation
    ? raw.map((observed: any) => buildLayerFromRestored(
      observed,
      opts.sessionId,
      observation?.mapspec_fingerprint,
      state.mapspec,
    ))
    : raw.map((layer: any) => ({
      ...layer,
      ...presentationFromMapSpec(state.mapspec, String(layer._mapspecLayerId ?? layer.id)),
    }));

  const keepers = allowedIds.size === 0
    ? layersToRestore
    : layersToRestore.filter((layer: any) => (
      allowedIds.has(String(layer.id))
      || allowedIds.has(String(layer._mapspecLayerId || ''))
    ));

  for (const layer of keepers) {
    store.addLayer(layer);
    if (
      layer._refId
      && layer._refId.startsWith('ref:')
      && !(
        layer._descriptor?.mvt_capable
        && layer._descriptor?.feature_count > 5000
      )
      && !(layer.source && typeof layer.source === 'object' && 'image' in layer.source)
    ) {
      apiFetch<GeoJSONFeatureCollection>(
        `/api/v1/layers/data/${encodeURIComponent(layer._refId)}?session_id=${encodeURIComponent(opts.sessionId)}`,
        { signal: opts.signal, ownerToken: opts.token ?? null, label: 'Layer data error' }
      )
        .then((geojson) => {
          if (opts.signal?.aborted) return;
          if (geojson && (geojson.type === 'FeatureCollection' || geojson.features)) {
            const current = useHudStore.getState().layers.find(
              (candidate) => candidate.id === layer.id
            );
            if (current?._refId === layer._refId) {
              useHudStore.getState().updateLayer(layer.id, { source: geojson });
            }
          }
        })
        .catch((err) => {
          const label = typeof layer.name === 'string' && layer.name
            ? layer.name
            : String(layer.id ?? layer._refId ?? '图层');
          reportLayerFetchFailure('[LayerFetch]', label, err);
        });
    }
  }
}

/**
 * 完整应用一份会话 map-state（视口 + 底图 + 图层）。分享回放页（/story）用：
 * 只读页面无在飞节流写入，直接 fly_to 即可（无需主应用 selectSession 的
 * viewport-seq 合并）；图层部分与主应用共用 restoreSessionMapLayers。
 */
export async function applyStoryMapState(
  state: SessionMapState,
  sessionId: string,
  signal: AbortSignal,
  dispatchAction: (action: MapActionPayload) => void,
): Promise<void> {
  const store = useHudStore.getState();
  if (state.base_layer) store.setBaseLayer(state.base_layer);
  const framed = selectCameraToRestore(state);
  if (framed) {
    dispatchAction({
      command: 'fly_to',
      params: framed,
    });
  }
  await restoreSessionMapLayers(state, { sessionId, token: null, signal });
}
