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
import { getPendingRemoved } from '@/lib/mapspec/session-cursor';
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
  const observedLayers = observationIsCurrent(state) && observation && Array.isArray(observation.layers)
    ? observation.layers
    : [];
  return observedLayers.length > 0 ? observedLayers : (state.layers || []);
}

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

/**
 * Live 会话的 committed MapSpec → HUD 行镜像（2026-08-25 会话回归）。
 * webgis_map_product / webgis_layer_upsert 等后端直写图层只落 MapSpec，
 * 不经 tool_result 的 addLayer 路径 → 图层面板看不到它们，ref 定向的
 * set_layer_status / finalize_display 也因 store 无行而漏网（POI 点隐藏
 * 失败即此因）。committed spec 提交时给缺行的图层补一行：行上带
 * `_mapspecLayerId`（开关/删除走 user-mutation 的 presentation/removed
 * 路径）与 `_refId`（数据由 ref-source-resolver 回填）。幂等——按 id
 * 与 `_mapspecLayerId` 双重去重，重复提交零副作用。
 */
export function syncSpecLayersToStore(
  mapspec: { layers?: unknown[]; sources?: Record<string, any> } | null | undefined,
  sessionId: string | undefined,
): void {
  const specLayers = mapspec?.layers;
  if (!Array.isArray(specLayers)) return;
  // #1078(G-9): 空层集不再早退 —— 全部层被移除时镜像行同样需要修剪，
  // 只有 add 循环天然跳过。

  const storeLayers = useHudStore.getState().layers ?? [];
  const known = new Set<string>();
  for (const row of storeLayers) {
    known.add(String(row.id));
    if (row._mapspecLayerId) known.add(String(row._mapspecLayerId));
  }

  // P1（幽灵面板行修复）：用户删除图层触发 double-superseded 时 pendingRemoved
  // 保留压制 compose（地图不渲被删层），但 committed spec 仍含已删层；下一个
  // 带 mapspec 的 SSE 事件若把同一层 re-add 为 store 行，面板重现幽灵行而地图
  // 不渲（pendingRemoved 仍过滤 compose）。跳过所有刚被 pendingRemoved 压制的
  // id 上的镜像（包括 alias），直到 pending 随下次收敛被清。
  const pendingIds = new Set(getPendingRemoved().map((pid) => String(pid)));

  for (const raw of specLayers) {
    const layer = raw as Record<string, any>;
    const id = String(layer?.id || '');
    if (!id || known.has(id)) continue;
    if (pendingIds.has(id)) continue;
    const source = mapspec?.sources?.[String(layer.source || '')] ?? {};
    const refId = typeof source?.ref_id === 'string' ? source.ref_id
      : typeof source?.ref === 'string' ? source.ref : undefined;
    // 命名链：spec 自带 name/title → legend 标题 → 算法语义名 → id 兜底。
    // 之前 product-* 直写层只能得到 "分析结果: <uuid 后缀>"，用户在面板
    // 里根本认不出哪个是 POI 查询结果。
    const algorithm = layer?.provenance?.algorithm;
    const name = String(
      layer.name
      || layer.title
      || layer?.legend_spec?.title
      || (algorithm === 'webgis_map_product' ? '地图产品图层' : '')
      || (algorithm ? `分析结果: ${algorithm}` : '')
      || `分析结果: ${id}`,
    );
    const presentation = presentationFromMapSpec(mapspec as any, id);
    useHudStore.getState().addLayer({
      id,
      name,
      type: layer.type === 'heatmap' || layer.type === 'raster' ? layer.type : 'vector',
      visible: presentation.visible !== false,
      opacity: typeof presentation.opacity === 'number' ? presentation.opacity : 1,
      group: 'analysis',
      source: {
        type: 'FeatureCollection',
        features: [],
        metadata: { ref_id: refId },
      } as GeoJSONFeatureCollection,
      _refId: refId,
      legend_spec: layer?.legend_spec,
      _mapspecLayerId: id,
      _tileUrl: refId && sessionId
        ? `${API_BASE}/api/v1/layers/data/${refId}/tiles/{z}/{x}/{y}.mvt?session_id=${sessionId}`
        : undefined,
    });
    known.add(id);
  }

  // #1078(G-9): spec 已不存在的层镜像行修剪 —— 后端驱动的层集改写（模板
  // 应用/替换/其它客户端突变）后，compose 已不渲该层面板行残留；勾选它
  // 会对服务端未知层发 patch。会话重入本就自愈（restore 的 allowedIds
  // 过滤），live 路径此前只增不删。保留：pending 压制行 / 过程层行 /
  // 非 spec 镜像的自建行（无 _mapspecLayerId 且 id 不在 spec——用户命令层）。
  const specIds = new Set(specLayers.map(
    (raw) => String((raw as Record<string, any>)?.id || ''),
  ));
  const keepRows = storeLayers.filter((row) => {
    if (!row._mapspecLayerId) return true;
    if (pendingIds.has(String(row._mapspecLayerId))) return true;
    const mirrored = specIds.has(String(row._mapspecLayerId))
      || specIds.has(String(row.id));
    return mirrored;
  });
  if (keepRows.length !== storeLayers.length) {
    useHudStore.getState().setLayers(keepRows);
  }
}

/** 把会话 map-state 的图层部分应用到 HUD store（含 ref 数据回填）。 */
export async function restoreSessionMapLayers(
  state: SessionMapState,
  opts: RestoreMapLayersOptions,
): Promise<void> {
  commitMapSpecDocument(state.mapspec, state._cartographic_mutation_revision);
  // 持久化 layers/observation 只记 HUD 行——product-* 等直写层只在
  // state.mapspec.layers 里，恢复时同样要镜像成行（与会话 live 路径
  // syncSpecLayersToStore 的调用点互补）。
  syncSpecLayersToStore(state.mapspec, opts.sessionId);
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
      // 旧持久化层的 name 可为 null（后端 runtime 层注册表早期写入）——
      // 图层面板此前直接渲染出 "undefined"。
      name: layer.name || `分析结果: ${layer._mapspecLayerId ?? layer.id}`,
      ...presentationFromMapSpec(state.mapspec, String(layer._mapspecLayerId ?? layer.id)),
    }));

  const hasMapSpecLayers = Array.isArray(state.mapspec?.layers);
  const keepers = !hasMapSpecLayers
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
