import maplibregl from 'maplibre-gl';

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
        if (l.id.startsWith(id)) {
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
