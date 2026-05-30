import maplibregl from 'maplibre-gl';
import { ThematicStyleDef } from './types';

/**
 * Safely adds or updates an image source.
 */
export function addImageSource(map: any, id: string, url: string, coordinates: [[number, number], [number, number], [number, number], [number, number]]) {
  const source = map.getSource(id) as maplibregl.ImageSource;
  if (source) {
    if (source.updateImage) {
      source.updateImage({ url, coordinates });
    }
  } else {
    map.addSource(id, {
      type: 'image',
      url,
      coordinates
    });
  }
}

/**
 * Safely adds or updates a GeoJSON source.
 */
export function addGeoJsonSource(map: any, id: string, data: any) {
  const source = map.getSource(id) as maplibregl.GeoJSONSource;
  if (source) {
    source.setData(data);
  } else {
    map.addSource(id, {
      type: 'geojson',
      data
    });
  }
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
export function addVectorLayer(map: any, options: VectorLayerOptions, beforeId?: string) {
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
  }, beforeId);
}

/**
 * Adds a thematic layer (choropleth or lisa) to the map using data-driven styling.
 */
export function addThematicLayer(map: any, id: string, data: any, styleDef: ThematicStyleDef, beforeId?: string) {
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
export function addNativeHeatmap(map: any, options: HeatmapOptions) {
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
 * Safely removes a layer and its corresponding source.
 * If prefix is true, removes all layers and the source starting with the id.
 */
export function removeLayerStack(map: any, id: string, prefix: boolean = false) {
  if (prefix) {
    const style = map.getStyle();
    if (style && style.layers) {
      style.layers.forEach((l: any) => {
        if (l.id === id || l.id.startsWith(id + '-')) {
          map.removeLayer(l.id);
        }
      });
    }
    if (map.getSource(id)) {
      map.removeSource(id);
    }
  } else {
    if (map.getLayer(id)) {
      map.removeLayer(id);
    }
    if (map.getSource(id)) {
      map.removeSource(id);
    }
  }
}

export interface StyleUpdateOptions {
  visibility?: 'visible' | 'none';
  opacity?: number;
  color?: string;
  strokeWidth?: number;
}

/**
 * Updates a layer's style properties.
 * Supports visibility, opacity, color, and stroke width.
 */
export function updateLayerStyle(map: any, id: string, style: StyleUpdateOptions) {
  if (!map.getLayer(id)) return;

  if (style.visibility) {
    map.setLayoutProperty(id, 'visibility', style.visibility);
  }

  const layer = map.getLayer(id);

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

  if (style.strokeWidth !== undefined) {
    if (layer.type === 'line') {
      map.setPaintProperty(id, 'line-width', style.strokeWidth);
    } else if (layer.type === 'circle') {
      map.setPaintProperty(id, 'circle-stroke-width', style.strokeWidth);
    }
  }
}

/**
 * Sets a filter on a specific layer.
 * filterExp should be a MapLibre filter expression.
 */
export function setLayerFilter(map: any, layerId: string, filterExp: any[]) {
  if (map.getLayer(layerId)) {
    map.setFilter(layerId, filterExp);
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
export function addRasterTileSource(map: any, id: string, urls: string | string[], tileSize: number = 256) {
  if (map.getSource(id)) return;
  const tiles = Array.isArray(urls) ? urls : [urls];
  map.addSource(id, { type: 'raster', tiles, tileSize });
}

/**
 * 把一组前缀匹配的子图层一次性切换可见性。
 * 等价于：遍历 style.layers，凡 id.startsWith(prefix) 的就 setLayoutProperty。
 */
export function setLayerStackVisibility(map: any, prefix: string, visible: boolean) {
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
  map: any,
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
 * 移除所有"孤儿"自定义图层及其 source：style 中以 prefix 开头但不属于 knownIds 的。
 *
 * `extractBaseId` 把 layer.id 切回它所属的"逻辑层 id"（map-panel 用
 * `custom-{layerId}-{sub}` 形式，stripPrefix 后再去掉最后一段 `-sub`）。
 * 不传时默认 `id => id`。
 */
export function removeOrphanCustomLayers(
  map: any,
  knownIds: Set<string>,
  prefix: string,
  extractBaseId: (idAfterPrefix: string) => string = (id) => id.replace(/-[^-]*$/, ''),
) {
  const style = map.getStyle();
  if (!style) return;

  // 先删 layer（layer 引用 source；先 source 后 layer 会报错）
  for (const l of style.layers || []) {
    if (l.id.startsWith(prefix)) {
      const base = extractBaseId(l.id.slice(prefix.length));
      if (!knownIds.has(base)) {
        try { map.removeLayer(l.id); } catch { /* silent */ }
      }
    }
  }
  for (const sid of Object.keys(style.sources || {})) {
    if (sid.startsWith(prefix)) {
      const base = sid.slice(prefix.length);
      if (!knownIds.has(base)) {
        try { map.removeSource(sid); } catch { /* silent */ }
      }
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
export function enable3DTerrain(map: any, options: TerrainOptions = {}) {
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
export function disable3DTerrain(map: any) {
  map.setTerrain(null);
}

/**
 * Z 顺序同步：按 orderedBaseIds 的顺序，把所有匹配前缀的子图层"按序 moveLayer"。
 *
 * MapLibre `moveLayer(id)` 无 beforeId 时把它移到栈顶。所以**反向迭代**
 * orderedBaseIds 即可让最后被 move 的（数组首）落在最顶。
 */
export function syncLayerZOrder(map: any, prefix: string, orderedBaseIds: string[]) {
  const style = map.getStyle();
  if (!style?.layers) return;
  // 反向：希望数组首的图层最终在最上面
  for (const baseId of [...orderedBaseIds].reverse()) {
    const sub = style.layers.filter((sl: any) => sl.id.startsWith(`${prefix}${baseId}`));
    for (const sl of sub) {
      try {
        if (map.getLayer(sl.id)) map.moveLayer(sl.id);
      } catch { /* silent */ }
    }
  }
}
