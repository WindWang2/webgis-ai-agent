import type { GeoJSONSource, ImageSource, Map } from 'maplibre-gl';
import { ThematicStyleDef } from './types';
import { filterFeaturesByBounds } from '@/lib/utils/geo';
import { useHudStore } from '@/lib/store/useHudStore';

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

function isMvtSourceId(id: string): boolean {
  // #692：对齐权威定义（adapter.isVectorTileLayer / layer-data）——
  // 此前缺 feature_count > 阈值判定，而 _tileUrl 对每个 geojson_ref 图层都设，
  // 导致 1k-5k 要素的 mvt_capable 图层永不做视口剔除（#668 的双裁剪守卫
  // 过匹配，Phase 8 剔除对中等规模图层失效）。阈值与 adapter 的
  // VECTOR_TILE_THRESHOLD 同值（跨模块 import 会引入 map-kit → runtime
  // 依赖环，此处注释锁定同值契约）。
  try {
    const layers: any[] = (useHudStore as any).getState?.()?.layers ?? [];
    const candidates = [id, id.replace(/^custom-/, '')];
    for (const cid of candidates) {
      const l = layers.find((x) => x.id === cid);
      if (!l?.['_tileUrl'] || !l?.['_descriptor']?.mvt_capable) continue;
      const fc = Number(l?.['_descriptor']?.feature_count ?? 0);
      if (fc > 5000) return true;
      const feats = l?.['source']?.features;
      if (Array.isArray(feats) && feats.length > 5000) return true;
    }
  } catch { /* ignore */ }
  return false;
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
  // #668: vector-tile sources are server-cropped per z/x/y — skip GeoJSON viewport double-crop
  const skipViewport = !!(options?.viewport && isMvtSourceId(id));
  const effective = options?.viewport && !skipViewport ? _filterForViewport(source, data, options.viewport) : data;
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
    if (isMvtSourceId(id)) return; // #668: double-crop guard
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

/**
 * #462: per-map layer-id ORDER registry. MapLibre has no public cheap
 * layer-order accessor — `map.getStyle()` deep-clones every layer (paint/layout
 * expressions included), which is multi-millisecond at 50+ sublayers. Hot paths
 * (per-reconcile z-order sync, per-command layer matching) only need the id
 * sequence, so we maintain it ourselves:
 *
 *  - `getStyleLayerIds(map)` seeds from ONE cold getStyle() the first time a
 *    map is seen, then serves the registry (no cloning).
 *  - Every mutation site we own calls noteStyleLayerAdded/Removed alongside
 *    its maplibre add/remove (renderer helpers, MapSpecRuntime, annotation +
 *    selection stacks, layer commands). `clearStyleLayerIds` is called on a
 *    base-style swap (setStyle drops everything without passing through any
 *    removal site — MapSpecRuntime.invalidateStyle).
 *  - Consumers must tolerate stale ids defensively (guard with map.getLayer /
 *    try-catch), mirroring what they already do for ids the style may have
 *    dropped mid-flight.
 */
const _styleLayerIdOrder = new WeakMap<object, string[]>();

/** Record a layer id as added (appended to the top of the z-order). */
export function noteStyleLayerAdded(map: object | null | undefined, id: string): void {
  if (!map) return;
  const ids = _styleLayerIdOrder.get(map);
  if (ids) {
    if (!ids.includes(id)) ids.push(id);
    return;
  }
  // Map not seeded yet: leave the cold seed to getStyleLayerIds — appending to
  // an unknown baseline could reorder unknown layers.
}

/** Record a layer id as removed from the style. */
export function noteStyleLayerRemoved(map: object | null | undefined, id: string): void {
  if (!map) return;
  const ids = _styleLayerIdOrder.get(map);
  if (!ids) return;
  const i = ids.indexOf(id);
  if (i >= 0) ids.splice(i, 1);
}

/** Record a layer id as moved to the top (anchorless moveLayer). */
export function noteStyleLayerMovedToTop(map: object | null | undefined, id: string): void {
  if (!map) return;
  const ids = _styleLayerIdOrder.get(map);
  if (!ids) return;
  const i = ids.indexOf(id);
  if (i >= 0) {
    ids.splice(i, 1);
    ids.push(id);
  }
}

/** Drop the registry for a map whose style was wholesale replaced (setStyle). */
export function clearStyleLayerIds(map: object | null | undefined): void {
  if (!map) return;
  _styleLayerIdOrder.delete(map);
}

/**
 * The style's layer ids in z-order (bottom → top). Registry-backed; the first
 * call for a map pays ONE cold getStyle() clone to seed, everything after is
 * maintained by the note* hooks. Returns an empty list for maps without a
 * style accessor.
 */
export function getStyleLayerIds(map: any): string[] {
  if (!map) return [];
  let ids = _styleLayerIdOrder.get(map);
  if (!ids) {
    ids = ((map.getStyle?.()?.layers ?? []) as any[]).map((l) => l.id as string);
    _styleLayerIdOrder.set(map, ids);
  }
  return ids;
}

/**
 * FE-P3-5: drop ALL registry entries (base-style reload wipes every source
 * without passing through removeSourceSafe). Reconcile re-registers the
 * sources it re-adds.
 */
export function unregisterAllGeoJsonSources() {
  _registeredGeoJsonSourceIds.clear();
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
    noteStyleLayerRemoved(map, options.id);
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
  noteStyleLayerAdded(map, options.id);
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
  } else if (styleDef.type === 'categorical') {
    // #557 断点 3：categorical style_def 的 categories 是 [{key,color,label}]
    // 列表（后端保留数值键类型，数值类别在此原样进入 match 表达式才能命中）。
    const categories = (styleDef.categories || []) as Array<{ key: string | number; color: string }>;
    colorExpression = ['match', ['get', styleDef.field]];
    
    for (const c of categories) {
      colorExpression.push(c.key);
      colorExpression.push(c.color);
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

/**
 * 停靠点位置是按 MapLibre 累积 shader 的实际密度域标定的：
 * val = weight × intensity × 0.39894 × exp(-4.5·d²)（高斯核，四边形铺 3σ），
 * 即 weight=1、intensity=1 的单个孤立点中心密度峰值只有 ≈0.4。
 * 旧停靠点 (0.2/0.4/…/1.0) 匀铺会让稀疏区整片停在首段蓝色（用户实测
 * "没有颜色分级"）；现在把中间色压到 0.12-0.45 段——单点中心落在绿段、
 * 3-5 点重叠到黄/橙、密集核 5+ 点冲到红段，任何缩放下都有多级渐变。
 * 位置表 [0, 0.12, 0.25, 0.45, 0.65, 0.85, 1] 与后端
 * app/tools/spatial.py _NATIVE_HEATMAP_COLORS（图例色）保持同源。
 */
const HEATMAP_STOP_POSITIONS = [0, 0.12, 0.25, 0.45, 0.65, 0.85, 1];

const HEATMAP_PALETTES = {
  classic: [
    'rgba(38,110,182,0)',
    'rgb(66,140,210)',
    'rgb(61,188,232)',
    'rgb(96,214,120)',
    'rgb(250,224,50)',
    'rgb(250,140,40)',
    'rgb(235,40,40)'
  ],
  magma: [
    'rgba(0,0,4,0)',
    'rgb(52,16,88)',
    'rgb(112,32,122)',
    'rgb(182,54,121)',
    'rgb(244,109,67)',
    'rgb(252,193,120)',
    'rgb(255,255,217)'
  ],
  viridis: [
    'rgba(68,1,84,0)',
    'rgb(72,40,120)',
    'rgb(59,92,157)',
    'rgb(35,148,139)',
    'rgb(122,203,98)',
    'rgb(253,213,60)',
    'rgb(255,255,220)'
  ],
  thermal: [
    'rgba(0,40,255,0)',
    'rgb(0,102,255)',
    'rgb(0,214,255)',
    'rgb(80,240,120)',
    'rgb(255,230,0)',
    'rgb(255,120,0)',
    'rgb(235,20,20)'
  ]
};

/** 命名调色板 → interpolate 停靠点序列 [pos, color, pos, color, ...]；未知键回落 classic。 */
function heatmapPaletteStops(palette: string | string[] | undefined): (number | string)[] {
  if (Array.isArray(palette)) {
    return palette.flatMap((color, i, arr) => [
      arr.length > 1 ? i / (arr.length - 1) : 0,
      color,
    ]);
  }
  const colors = HEATMAP_PALETTES[palette as keyof typeof HEATMAP_PALETTES] ?? HEATMAP_PALETTES.classic;
  return HEATMAP_STOP_POSITIONS.flatMap((pos, i) => [pos, colors[i]]);
}

export interface HeatmapOptions {
  id: string;
  source: string;
  /** 命名键（classic/magma/viridis/thermal，未知名回落 classic）或颜色数组。 */
  palette?: string | string[];
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
    noteStyleLayerRemoved(map, options.id);
  }

  const palette = heatmapPaletteStops(options.palette);

  // 后端部分调用方把「米」半径（如 2000m）直接塞进 radius —— MapLibre 的
  // heatmap-radius 是像素。>100 视为米制误传，回落默认；最终收敛到 [4, 80]px。
  const rawRadius = Number(options.radius ?? 30)
  const radiusPx = Math.max(4, Math.min(80, Number.isFinite(rawRadius) && rawRadius <= 100 ? rawRadius : 30))

  // 显式 intensity 原样透传；缺省时随 zoom 增益：高斯核在放大、点距拉开后
  // 重叠贡献变少，不补偿的话放大后整图退回低密度冷色（MapLibre 官方示例
  // 同样用 zoom-interpolate intensity 1→3）。
  const intensity = options.intensity ?? [
    'interpolate', ['linear'], ['zoom'],
    4, 0.8,
    10, 1.3,
    14, 2.2,
  ];

  map.addLayer({
    id: options.id,
    type: 'heatmap',
    source: options.source,
    paint: {
      'heatmap-weight': options.weight ?? 1,
      'heatmap-intensity': intensity as any,
      'heatmap-color': [
        'interpolate',
        ['linear'],
        ['heatmap-density'],
        ...palette
      ],
      'heatmap-radius': radiusPx,
      'heatmap-opacity': options.opacity || 1
    }
  });
  noteStyleLayerAdded(map, options.id);
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
    noteStyleLayerRemoved(map, lid);
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
  /** #557 断点 5：模板 fillOpacity 直达 fill-opacity paint（此前被丢弃）。 */
  fillOpacity?: number;
  color?: string;
  strokeColor?: string;
  strokeWidth?: number;
  pointSize?: number;
  dashArray?: string;
  fill?: boolean;
  /** #557 断点 3：categorical 符号化 —— colorMap 生成 match 表达式。 */
  categorical?: {
    field: string;
    colorMap: Record<string, string>;
    fillOpacity?: number;
    strokeWidth?: number;
  };
}

/**
 * Updates a layer's style properties.
 * Supports visibility, opacity, fillOpacity, color, strokeColor, strokeWidth,
 * pointSize, dashArray, fill, and categorical match-expression paints.
 */
export function updateLayerStyle(map: Map, id: string, style: StyleUpdateOptions) {
  if (!map.getLayer(id)) return;

  if (style.visibility) {
    map.setLayoutProperty(id, 'visibility', style.visibility);
  }

  const layer = map.getLayer(id);
  if (!layer) return;

  // #557 断点 5：fillOpacity 单独键 → fill-opacity（fill 图层）。
  if (style.fillOpacity !== undefined && layer.type === 'fill') {
    map.setPaintProperty(id, 'fill-opacity', style.fillOpacity);
  }

  // #557 断点 3：categorical 分支 —— ['match', ['get', field], v1, c1, …, fallback]。
  if (style.categorical) {
    const { field, colorMap, fillOpacity: catFillOpacity } = style.categorical;
    if (field && colorMap) {
      const match: any[] = ['match', ['get', field]];
      for (const [key, color] of Object.entries(colorMap)) {
        match.push(key, color);
      }
      match.push('#cccccc');
      const colorProp =
        layer.type === 'fill' ? 'fill-color'
        : layer.type === 'circle' ? 'circle-color'
        : layer.type === 'line' ? 'line-color'
        : null;
      if (colorProp) {
        map.setPaintProperty(id, colorProp, match);
      }
    }
    if (catFillOpacity !== undefined && layer.type === 'fill') {
      map.setPaintProperty(id, 'fill-opacity', catFillOpacity);
    }
  }

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
        noteStyleLayerRemoved(map, l.id);
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
  const value = visible ? 'visible' : 'none';
  // #462: id list from the maintained registry — no style deep-clone per toggle.
  for (const id of getStyleLayerIds(map)) {
    if (id.startsWith(prefix)) {
      try {
        map.setLayoutProperty(id, 'visibility', value);
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
  noteStyleLayerAdded(map, `process-${stepId}-fill`);
  noteStyleLayerAdded(map, `process-${stepId}-line`);
  noteStyleLayerAdded(map, `process-${stepId}-point`);
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
        noteStyleLayerRemoved(map, l.id);
      }
    } else if (lSource && orphanSourceIds.has(lSource)) {
      try { map.removeLayer(l.id); } catch { /* silent */ }
      noteStyleLayerRemoved(map, l.id);
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
  // #462: the id ORDER comes from the maintained registry — MapSpecRuntime
  // calls this after every layer-changing patch, and map.getStyle() would
  // deep-clone the whole style each time. Consumers guard with getLayer for
  // ids the style may have dropped since the last note.
  const layerIds = getStyleLayerIds(map);
  if (layerIds.length === 0) return;
  // 反向：希望数组首的图层最终在最上面
  for (const baseId of [...orderedBaseIds].reverse()) {
    const fullPrefix = prefix ? `${prefix}${baseId}` : baseId;
    const sub = layerIds.filter((id) => {
      return id === fullPrefix || id.startsWith(`${fullPrefix}__`) || id.startsWith(`${fullPrefix}-`);
    });
    for (const id of sub) {
      try {
        if (map.getLayer(id)) {
          map.moveLayer(id);
          noteStyleLayerMovedToTop(map, id);
        }
      } catch { /* silent */ }
    }
  }
}

/**
 * #461: z-band prefix of the imperative command overlays (`add_layer`,
 * `add_native_heatmap`, `create_thematic_map`, `add_heatmap_raster`,
 * `add_raster_layer` all mint `custom-*` ids — see layerCommands.ts /
 * heatmapCommands.ts).
 */
export const CUSTOM_OVERLAY_PREFIX = 'custom-';

/**
 * #461 (sibling of #401): re-raise the imperative `custom-*` overlays above
 * the spec-derived layers after every layer-changing reconcile.
 *
 * syncLayerZOrder moves every MapSpec sublayer to the TOP of the stack on any
 * non-empty layer patch, burying the command-added overlays under
 * 0.3-opacity fills. #401 fixed that class of bug for the annotation stack
 * only — the custom band had no post-reconcile restoration. Because
 * useMapBridge dispatches imperative commands BEFORE the same step_result's
 * store layer mount, a command emitted alongside an auto-mount is buried by
 * that very reconcile; nothing ever lifted it again.
 *
 * Call after a reconcile settles (alongside the highlight/annotation raises).
 * Iteration in style order preserves the overlays' relative z among
 * themselves; moveLayer is guarded + wrapped so a layer that vanished
 * mid-reconcile is skipped silently.
 */
export function raiseCustomOverlayLayers(map: Map): void {
  // #462: id list from the maintained registry (runs after every reconcile —
  // no style deep-clone on the hot path). Snapshot before mutating: moveLayer
  // reorders the sequence being scanned.
  const customIds = getStyleLayerIds(map).filter((id) =>
    id.startsWith(CUSTOM_OVERLAY_PREFIX),
  );
  for (const id of customIds) {
    if (!map.getLayer(id)) continue;
    try {
      map.moveLayer(id);
      noteStyleLayerMovedToTop(map, id);
    } catch { /* silent */ }
  }
}
