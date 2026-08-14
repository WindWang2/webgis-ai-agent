import type { GeoJSONSource, ImageSource, Map } from 'maplibre-gl';
import { ThematicStyleDef } from './types';
import { filterFeaturesByBounds } from '@/lib/utils/geo';

/**
/**
 * 审计 F31：缓存每个 source 上次 setData 的 data 引用。
 * 相同引用跳过 setData，避免每帧重新解析 GeoJSON（50k 要素层 ~100ms jank）。
 * 用 WeakMap 让 source 被 GC 时自动清理。
 */
const _lastGeoJsonData = new WeakMap<object, unknown>();

/**
 * 审计 F28：缓存每个 image source 上次用的 url。
 * 如果 url 相同，MapLibre 的 ImageSource 会用内部缓存不重新拉取 ->
 * 后端在同一 URL 覆盖文件时用户看到旧图。加 ?v=cacheBuster 强制刷新。
 * 用 WeakMap 让 source 被 GC 时自动清理。
 */
const _lastImageUrl = new WeakMap<object, string>();

/**
 * Phase 8: viewport-driven feature culling for large inline GeoJSON sources.
 *
 * filterFeaturesByBounds (lib/utils/geo.ts) trims a big FeatureCollection to
 * the features intersecting the visible bounds BEFORE setData, so MapLibre
 * parses a fraction of the features on each viewport change. Design:
 *
 *  - addGeoJsonSource(..., { viewport }) filters on add/update; the ORIGINAL
 *    (unfiltered) data is kept in _rawDataBySource so a later viewport change
 *    can re-filter from scratch.
 *  - refreshGeoJsonSourcesByViewport() re-filters every registered inline
 *    source against the current bounds — call it from the map's debounced
 *    move handler (map-panel.tsx).
 *  - Filtered results are cached per source + exact viewport (floating-point
 *    equality is fine: the same map state yields the same bounds), so a
 *    stable viewport never re-runs setData (preserving the F31 fast path).
 *  - Small sources (< filterFeaturesByBounds' minFilter) pass through
 *    unchanged — same reference, same F31 skip.
 */
export type ViewportBBox = [number, number, number, number];

const _rawDataBySource = new WeakMap<object, unknown>();
const _filteredBySource = new WeakMap<object, { data: unknown; viewport: ViewportBBox }>();

function sameViewport(a: ViewportBBox, b: ViewportBBox): boolean {
  return a[0] === b[0] && a[1] === b[1] && a[2] === b[2] && a[3] === b[3];
}

function _filterForViewport(source: object | undefined, data: any, viewport: ViewportBBox): unknown {
  // Before addSource the source object doesn't exist yet — nothing to cache
  // against (WeakMap keys must be objects), and this only happens once per id.
  if (!source) return filterFeaturesByBounds(data, viewport);
  const cached = _filteredBySource.get(source);
  if (cached && sameViewport(cached.viewport, viewport)) {
    return cached.data;
  }
  const effective = filterFeaturesByBounds(data, viewport);
  _filteredBySource.set(source, { data: effective, viewport: [...viewport] });
  return effective;
}

/**
 * Safely adds or updates an image source.
 */
export function addImageSource(map: Map, id: string, url: string, coordinates: [[number, number], [number, number], [number, number], [number, number]]) {
  const source = map.getSource(id) as ImageSource;
  if (source) {
    if (source.updateImage) {
      // 审计 F28：同 url 加 cache-buster，防 MapLibre 内部缓存命中显示旧图
      const lastUrl = _lastImageUrl.get(source);
      const effectiveUrl = lastUrl === url
        ? `${url}${url.includes('?') ? '&' : '?'}_=${Date.now()}`
        : url;
      _lastImageUrl.set(source, url);  // 记录原始 url（不含 cache-buster）
      source.updateImage({ url: effectiveUrl, coordinates });
    }
  } else {
    map.addSource(id, {
      type: 'image',
      url,
      coordinates
    });
    const newSource = map.getSource(id);
    if (newSource) _lastImageUrl.set(newSource, url);
  }
}

/**
 * Safely adds or updates a GeoJSON source.
 *
 * 审计 F31：如果 data 引用与上次相同，跳过 setData -- MapLibre 的 setData
 * 即使 data 引用相同也会触发全量重新解析。
 *
 * Phase 8: optional `viewport` bounds trim large inline FeatureCollections to
 * the visible area before setData (see module comment). The ORIGINAL data is
 * retained in _rawDataBySource for later re-filtering.
 */
const _registeredGeoJsonSourceIds = new Set<string>();

export function addGeoJsonSource(map: Map, id: string, data: any, options?: { viewport?: ViewportBBox }) {
  const source = map.getSource(id) as GeoJSONSource;
  const effective = options?.viewport ? _filterForViewport(source, data, options.viewport) : data;
  if (source) {
    // 引用相同则跳过（最常见的优化 -- 大量 layer 重新渲染时）
    if (_lastGeoJsonData.get(source) === effective) return;
    _lastGeoJsonData.set(source, effective);
    source.setData(effective as any);
    _rawDataBySource.set(source, data);
    _registeredGeoJsonSourceIds.add(id);
  } else {
    map.addSource(id, {
      type: 'geojson',
      data: effective
    });
    // 新 source 也记录引用，便于后续比较
    const newSource = map.getSource(id);
    if (newSource) {
      _lastGeoJsonData.set(newSource, effective);
      _rawDataBySource.set(newSource, data);
      _registeredGeoJsonSourceIds.add(id);
      if (options?.viewport) {
        _filteredBySource.set(newSource, { data: effective, viewport: [...options.viewport] });
      }
    }
  }
}

/**
 * Re-filter every registered inline GeoJSON source against the current
 * viewport bounds and setData the trimmed result. Sources added with a
 * viewport (large inline FeatureCollections) are re-filtered from their
 * ORIGINAL data; small sources and tile/url sources are skipped (no raw data
 * registered — and small collections pass through unchanged anyway).
 *
 * Direct Set lookup avoids cloning the entire MapLibre stylesheet on every
 * camera move frame.
 */
export function refreshGeoJsonSourcesByViewport(map: Map, viewport: ViewportBBox) {
  if (!map) return;
  _registeredGeoJsonSourceIds.forEach((id) => {
    const source = map.getSource?.(id) as GeoJSONSource;
    if (!source) return;
    const raw = _rawDataBySource.get(source);
    if (raw === undefined) return; // tile/url source — nothing to trim
    const effective = _filterForViewport(source, raw, viewport);
    if (_lastGeoJsonData.get(source) !== effective) {
      _lastGeoJsonData.set(source, effective);
      source.setData(effective as any);
    }
  });
}

export function unregisterGeoJsonSource(id: string) {
  _registeredGeoJsonSourceIds.delete(id);
}

export interface VectorLayerOptions {
  id: string;
  source: string;
  type: 'circle' | 'line' | 'fill' | 'raster';
  paint?: any;
  layout?: any;
  minzoom?: number;
  maxzoom?: number;
  filter?: any[];
}

/**
 * Adds a vector layer (circle, line, or fill) to the map.
 * Removes existing layer with the same ID if it exists.
 */
export function addVectorLayer(map: Map, options: VectorLayerOptions, beforeId?: string) {
  if (map.getLayer(options.id)) {
    map.removeLayer(options.id);
  }

  map.addLayer({
    id: options.id,
    type: options.type,
    source: options.source,
    paint: options.paint || {},
    layout: options.layout || {},
    ...(options.minzoom !== undefined && { minzoom: options.minzoom }),
    ...(options.maxzoom !== undefined && { maxzoom: options.maxzoom }),
    ...(options.filter && { filter: options.filter }),
  } as any, beforeId);
}

/**
 * Adds a thematic layer (choropleth or lisa) to the map using data-driven styling.
 */
export function addThematicLayer(map: Map, id: string, data: any, styleDef: ThematicStyleDef, beforeId?: string) {
  const geomType = styleDef.geometry_type || 'Polygon';
  const layerType = geomType === 'Point' ? 'circle' : 'fill';
  
  let colorExpression: any;
  
  if (styleDef.type === 'choropleth') {
    const breaks = styleDef.breaks || [];
    const colors = styleDef.colors || [];
    
    // Default fallback if colors is empty, though backend should provide it
    if (breaks.length > 0 && colors.length > 0) {
      colorExpression = ['step', ['get', styleDef.field]];
      colorExpression.push(colors[0]); // Base color for values < first break
      
      // Step expression alternates: base_color, break1, color1, break2, color2...
      for (let i = 0; i < breaks.length; i++) {
        colorExpression.push(breaks[i]);
        colorExpression.push(colors[Math.min(i + 1, colors.length - 1)]);
      }
    } else {
      colorExpression = colors[0] || '#ccc';
    }
  } else if (styleDef.type === 'lisa') {
    const categories = styleDef.categories || {};
    colorExpression = ['match', ['get', styleDef.field]];
    
    for (const [key, color] of Object.entries(categories)) {
      colorExpression.push(key);
      colorExpression.push(color);
    }
    
    // Add default color for unmatched values
    colorExpression.push('#cccccc');
  } else {
    colorExpression = '#cccccc';
  }

  const paint: any = {};
  if (layerType === 'fill') {
    paint['fill-color'] = colorExpression;
    paint['fill-opacity'] = 0.8;
  } else {
    paint['circle-color'] = colorExpression;
    paint['circle-opacity'] = 0.8;
    paint['circle-radius'] = 6;
  }

  addVectorLayer(map, {
    id,
    type: layerType,
    source: id,
    paint
  }, beforeId);
}

const HEATMAP_PALETTES = {
  classic: [
    0, 'rgba(33,102,172,0)',
    0.2, 'rgb(103,169,207)',
    0.4, 'rgb(209,229,240)',
    0.6, 'rgb(253,219,199)',
    0.8, 'rgb(239,138,98)',
    1, 'rgb(178,24,43)'
  ],
  magma: [
    0, 'rgba(0,0,4,0)',
    0.2, 'rgb(81,18,124)',
    0.4, 'rgb(182,54,121)',
    0.6, 'rgb(251,136,97)',
    0.8, 'rgb(252,253,191)',
    1, 'rgb(255,255,255)'
  ],
  viridis: [
    0, 'rgba(68,1,84,0)',
    0.2, 'rgb(59,82,139)',
    0.4, 'rgb(33,145,140)',
    0.6, 'rgb(94,201,98)',
    0.8, 'rgb(253,231,37)',
    1, 'rgb(255,255,255)'
  ],
  thermal: [
    0, 'rgba(0,0,255,0)',
    0.2, 'rgb(0,255,255)',
    0.4, 'rgb(0,255,0)',
    0.6, 'rgb(255,255,0)',
    0.8, 'rgb(255,0,0)',
    1, 'rgb(255,255,255)'
  ]
};

export interface HeatmapOptions {
  id: string;
  source: string;
  palette?: keyof typeof HEATMAP_PALETTES;
  radius?: number;
  weight?: any;
  intensity?: number;
  opacity?: number;
}

/**
 * Adds a native MapLibre heatmap layer.
 */
export function addNativeHeatmap(map: Map, options: HeatmapOptions) {
  if (map.getLayer(options.id)) {
    map.removeLayer(options.id);
  }

  const palette = HEATMAP_PALETTES[options.palette || 'classic'];

  map.addLayer({
    id: options.id,
    type: 'heatmap',
    source: options.source,
    paint: {
      'heatmap-weight': options.weight || 1,
      'heatmap-intensity': options.intensity || 1,
      'heatmap-color': [
        'interpolate',
        ['linear'],
        ['heatmap-density'],
        ...palette
      ],
      'heatmap-radius': options.radius || 30,
      'heatmap-opacity': options.opacity || 1
    }
  });
}

/**
 * Safely removes a layer stack and its corresponding source(s) and image texture(s).
 * Ensures all dependent layers are detached before sources are removed.
 * If prefix is true, removes all layers and sources matching or starting with the id.
 *
 * Returns `false` when any removal call threw (round-2 FIX-B: callers use this
 * to distinguish a real removal failure from a no-op, instead of the old silent
 * swallow). Layers/sources that are already gone are skipped, not counted as
 * failures — MapLibre throws on removeLayer/removeSource of a missing id.
 */
export function removeLayerStack(map: Map, id: string, prefix: boolean = false): boolean {
  const style = map.getStyle();
  const targetLayerIds = new Set<string>();
  const targetSourceIds = new Set<string>();

  if (prefix) {
    style?.layers?.forEach((l: any) => {
      if (l.id === id || l.id.startsWith(id + '-') || l.id.startsWith(id + '_')) {
        targetLayerIds.add(l.id);
      }
    });
    if (style?.sources) {
      Object.keys(style.sources).forEach((sid) => {
        if (sid === id || sid.startsWith(id + '-') || sid.startsWith(id + '_')) {
          targetSourceIds.add(sid);
        }
      });
    }
    // Also include id directly in case style index doesn't list it
    targetLayerIds.add(id);
    targetSourceIds.add(id);
  } else {
    // Single layer/source mode: include id if present on map or style
    if (map.getLayer?.(id) || style?.layers?.some((l: any) => l.id === id)) {
      targetLayerIds.add(id);
    }
    if (map.getSource?.(id) || (style?.sources && id in style.sources)) {
      targetSourceIds.add(id);
    }
  }

  // Collect any layers referencing any of the target sources to ensure proper detachment
  style?.layers?.forEach((l: any) => {
    if (l.source && targetSourceIds.has(l.source)) {
      targetLayerIds.add(l.id);
    }
  });

  // A layer/source is "present" when the live map reports it OR the style index
  // lists it (they can disagree mid style swap). MapLibre throws on
  // removeLayer/removeSource of a missing id, so never attempt removals for ids
  // that are not known to exist — that also keeps `ok` meaningful (only real
  // removal failures flip it, never no-op attempts).
  const styleLayerIds = new Set((style?.layers ?? []).map((l: any) => l.id as string));
  const styleSourceIds = new Set(Object.keys(style?.sources ?? {}));

  // 1. Remove all dependent layers first to detach from sources
  let ok = true;
  targetLayerIds.forEach((lid) => {
    if (!map.getLayer?.(lid) && !styleLayerIds.has(lid)) return; // already gone
    try { map.removeLayer(lid); } catch { ok = false; }
  });

  // 2. Remove target sources and cleanup any registered image textures
  targetSourceIds.forEach((sid) => {
    _registeredGeoJsonSourceIds.delete(sid);
    if (!map.getSource?.(sid) && !styleSourceIds.has(sid)) return;
    try { map.removeSource(sid); } catch { ok = false; }
    if (typeof map.hasImage === 'function' && map.hasImage(sid)) {
      try { map.removeImage(sid); } catch { ok = false; }
    }
  });
  return ok;
}

export interface StyleUpdateOptions {
  visibility?: 'visible' | 'none';
  opacity?: number;
  color?: string;
  strokeColor?: string;
  strokeWidth?: number;
  pointSize?: number;
  dashArray?: string;
  fill?: boolean;
}

/**
 * Updates a layer's style properties.
 * Supports visibility, opacity, color, strokeColor, strokeWidth, pointSize, dashArray, fill.
 */
export function updateLayerStyle(map: Map, id: string, style: StyleUpdateOptions) {
  if (!map.getLayer(id)) return;

  if (style.visibility) {
    map.setLayoutProperty(id, 'visibility', style.visibility);
  }

  const layer = map.getLayer(id);
  if (!layer) return;

  if (style.opacity !== undefined) {
    let opacityProp = '';
    switch (layer.type) {
      case 'fill': opacityProp = 'fill-opacity'; break;
      case 'line': opacityProp = 'line-opacity'; break;
      case 'circle': opacityProp = 'circle-opacity'; break;
      case 'heatmap': opacityProp = 'heatmap-opacity'; break;
      case 'raster': opacityProp = 'raster-opacity'; break;
      case 'symbol': opacityProp = 'icon-opacity'; break;
    }
    if (opacityProp) {
      map.setPaintProperty(id, opacityProp, style.opacity);
    }
  }

  if (style.color) {
    let colorProp = '';
    switch (layer.type) {
      case 'fill': colorProp = 'fill-color'; break;
      case 'line': colorProp = 'line-color'; break;
      case 'circle': colorProp = 'circle-color'; break;
    }
    if (colorProp) {
      map.setPaintProperty(id, colorProp, style.color);
    }
  }

  if (style.strokeColor) {
    if (layer.type === 'fill') {
      map.setPaintProperty(id, 'fill-outline-color', style.strokeColor);
    } else if (layer.type === 'circle') {
      map.setPaintProperty(id, 'circle-stroke-color', style.strokeColor);
    } else if (layer.type === 'line') {
      map.setPaintProperty(id, 'line-color', style.strokeColor);
    }
  }

  if (style.strokeWidth !== undefined) {
    if (layer.type === 'line') {
      map.setPaintProperty(id, 'line-width', style.strokeWidth);
    } else if (layer.type === 'circle') {
      map.setPaintProperty(id, 'circle-stroke-width', style.strokeWidth);
    }
  }

  if (style.pointSize !== undefined && layer.type === 'circle') {
    map.setPaintProperty(id, 'circle-radius', style.pointSize);
  }

  if (style.dashArray && layer.type === 'line') {
    // MapLibre line-dasharray 接受任意长度的 number 数组（odd-length 会被
    // 自动重复），所以类型用 number[] 而非 tuple —— 之前 tuple 阻止了
    // dashdot 的 4 段 pattern，导致 TS2322 + Docker 构建失败。
    const patterns: Record<string, number[]> = {
      dashed: [4, 2],
      dotted: [1, 2],
      dashdot: [4, 2, 1, 2],
    };
    const pattern = patterns[style.dashArray];
    if (pattern) {
      map.setPaintProperty(id, 'line-dasharray', pattern);
    }
  }
}

/**
 * Sets a filter on a specific layer.
 * filterExp should be a MapLibre filter expression.
 */
export function setLayerFilter(map: Map, layerId: string, filterExp: any[]) {
  if (map.getLayer(layerId)) {
    map.setFilter(layerId, filterExp as any);
  } else {
    throw new Error(`Layer '${layerId}' not found.`);
  }
}

// ─────────────────────────────────────────────────────────────
// M4 扩展：把 map-panel.tsx 内联的 MapLibre 调用收敛到 renderer
// ─────────────────────────────────────────────────────────────

/**
 * 添加（或忽略已存在的）瓦片栅格源。
 * 主要给底图/外部 tile 服务用，替代 map-panel 里手写的
 * `map.addSource({type:'raster', tiles:[url], tileSize:256})`。
 */
export function addRasterTileSource(map: Map, id: string, urls: string | string[], tileSize: number = 256) {
  if (map.getSource(id)) return;
  const tiles = Array.isArray(urls) ? urls : [urls];
  map.addSource(id, { type: 'raster', tiles, tileSize });
}

/**
 * Data Plane: MVT 矢量瓦片源。若同 id 已有非 vector 源（大图层从空
 * GeoJSON 升级为瓦片），先移除旧源及其依赖图层（reconcile 的 layer ops
 * 会按新 spec 重建）。
 */
export function addVectorTileSource(map: Map, id: string, tiles: string[], minzoom?: number, maxzoom?: number) {
  const existing = map.getSource(id);
  if (existing && (existing as any).type === 'vector') return;
  if (existing) {
    const style = map.getStyle();
    for (const l of style?.layers ?? []) {
      if ((l as any).source === id && map.getLayer(l.id)) {
        try { map.removeLayer(l.id); } catch { /* already gone */ }
      }
    }
    try { map.removeSource(id); } catch { /* already gone */ }
  }
  map.addSource(id, { type: 'vector', tiles, minzoom, maxzoom } as any);
}

/**
 * 把一组前缀匹配的子图层一次性切换可见性。
 * 等价于：遍历 style.layers，凡 id.startsWith(prefix) 的就 setLayoutProperty。
 */
export function setLayerStackVisibility(map: Map, prefix: string, visible: boolean) {
  const style = map.getStyle();
  if (!style?.layers) return;
  const value = visible ? 'visible' : 'none';
  for (const l of style.layers) {
    if (l.id.startsWith(prefix)) {
      try {
        map.setLayoutProperty(l.id, 'visibility', value);
      } catch {
        /* layer 可能在迭代过程中被另一个 effect 移走，吃掉 */
      }
    }
  }
}

export interface ProcessLayerStyle {
  /** 主色 — 默认绿色 */
  color?: string;
  /** 多边形填充透明度 0~1，默认 0.08 */
  fillOpacity?: number;
}

/**
 * 添加"过程层"三件套（fill + dashed line + point），用来可视化中间步骤。
 * 每个 stepId 独立 source，前缀 `process-{stepId}-{fill|line|point}`。
 */
export function addProcessLayerStack(
  map: Map,
  stepId: string,
  geojson: any,
  style: ProcessLayerStyle = {},
) {
  const sourceId = `process-${stepId}`;
  if (map.getSource(sourceId)) return;

  const color = style.color || '#16a34a';
  const fillOpacity = style.fillOpacity ?? 0.08;

  map.addSource(sourceId, { type: 'geojson', data: geojson });
  map.addLayer({
    id: `process-${stepId}-fill`,
    type: 'fill',
    source: sourceId,
    paint: {
      'fill-color': `rgba(22, 163, 74, ${fillOpacity})`,
      'fill-outline-color': 'rgba(22, 163, 74, 0.3)',
    },
  });
  map.addLayer({
    id: `process-${stepId}-line`,
    type: 'line',
    source: sourceId,
    paint: {
      'line-color': color,
      'line-width': 1.5,
      'line-opacity': 0.4,
      'line-dasharray': [3, 3],
    },
  });
  map.addLayer({
    id: `process-${stepId}-point`,
    type: 'circle',
    source: sourceId,
    filter: ['==', '$type', 'Point'],
    paint: {
      'circle-radius': 4,
      'circle-color': 'rgba(22, 163, 74, 0.3)',
      'circle-stroke-width': 1,
      'circle-stroke-color': color,
    },
  });
}

/**
 * 移除所有"孤儿"自定义图层及其 source 及 image texture：style 中以 prefix 开头 unsuccessfully matched knownIds 的。
 *
 * `extractBaseId` 把 layer.id 切回它所属的"逻辑层 id"（map-panel 用
 * `custom-{layerId}-{sub}` 形式，stripPrefix 后再去掉最后一段 `-sub`）。
 * 不传时默认 `id => id`。
 */
export function removeOrphanCustomLayers(
  map: Map,
  knownIds: Set<string>,
  prefix: string,
  extractBaseId: (idAfterPrefix: string) => string = (id) => id.replace(/[-_][^-_]*$/, ''),
) {
  const style = map.getStyle();
  if (!style) return;

  const orphanSourceIds = new Set<string>();
  for (const sid of Object.keys(style.sources || {})) {
    if (sid.startsWith(prefix)) {
      const base = sid.slice(prefix.length);
      if (!knownIds.has(base)) {
        orphanSourceIds.add(sid);
      }
    }
  }

  // 先删 layer（layer 引用 source；先 source 后 layer 会报错）
  for (const l of style.layers || []) {
    const lSource = (l as any).source as string | undefined;
    if (l.id.startsWith(prefix)) {
      const base = extractBaseId(l.id.slice(prefix.length));
      if (!knownIds.has(base) || (lSource && orphanSourceIds.has(lSource))) {
        try { map.removeLayer(l.id); } catch { /* silent */ }
      }
    } else if (lSource && orphanSourceIds.has(lSource)) {
      try { map.removeLayer(l.id); } catch { /* silent */ }
    }
  }

  for (const sid of Array.from(orphanSourceIds)) {
    try { map.removeSource(sid); } catch { /* silent */ }
    if (map.hasImage?.(sid)) {
      try { map.removeImage(sid); } catch { /* silent */ }
    }
  }
}

export interface TerrainOptions {
  /** 等高线/DEM 瓦片 URL — 默认 AWS terrarium */
  url?: string;
  /** 立体强度，>1 拔高，<1 压低 */
  exaggeration?: number;
  /** sourceId — 默认 'terrain-aws'。换源时记得传不同 id 否则会跟旧源冲突 */
  sourceId?: string;
}

/**
 * 启用 3D 地形 —— 添加 raster-dem source 并调 setTerrain。
 * 幂等：source 已存在时直接复用，不重复 addSource。
 */
export function enable3DTerrain(map: Map, options: TerrainOptions = {}) {
  const sourceId = options.sourceId || 'terrain-aws';
  const url = options.url || 'https://s3.amazonaws.com/elevation-tiles-prod/terrarium/{z}/{x}/{y}.png';
  if (!map.getSource(sourceId)) {
    map.addSource(sourceId, {
      type: 'raster-dem',
      tiles: [url],
      tileSize: 256,
      maxzoom: 14,
    });
  }
  map.setTerrain({ source: sourceId, exaggeration: options.exaggeration ?? 1.5 });
}

/** 关闭 3D 地形（保留 source 以便快速重启）。 */
export function disable3DTerrain(map: Map) {
  map.setTerrain(null);
}

/**
 * Z 顺序同步：按 orderedBaseIds 的顺序，把所有匹配前缀的子图层"按序 moveLayer"。
 *
 * MapLibre `moveLayer(id)` 无 beforeId 时把它移到栈顶。所以**反向迭代**
 * orderedBaseIds 即可让最后被 move 的（数组首）落在最顶。
 */
export function syncLayerZOrder(map: Map, prefix: string, orderedBaseIds: string[]) {
  const style = map.getStyle();
  if (!style?.layers) return;
  // 反向：希望数组首的图层最终在最上面
  for (const baseId of [...orderedBaseIds].reverse()) {
    const fullPrefix = prefix ? `${prefix}${baseId}` : baseId;
    const sub = style.layers.filter((sl: any) => {
      const id = sl.id as string;
      return id === fullPrefix || id.startsWith(`${fullPrefix}__`) || id.startsWith(`${fullPrefix}-`);
    });
    for (const sl of sub) {
      try {
        if (map.getLayer(sl.id)) map.moveLayer(sl.id);
      } catch { /* silent */ }
    }
  }
}
