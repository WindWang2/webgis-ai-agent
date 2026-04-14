/**
 * Core shared types for WebGIS AI Agent
 */

// === GeoJSON types ===

export interface GeoJSONGeometry {
  type: string;
  coordinates: unknown;
}

export interface GeoJSONFeature {
  type: 'Feature';
  geometry: GeoJSONGeometry | null;
  properties: Record<string, unknown>;
}

export interface GeoJSONFeatureCollection {
  type: 'FeatureCollection';
  features: GeoJSONFeature[];
  metadata?: Record<string, unknown>;
}

// === Tool result types ===

export interface ToolResult {
  type?: string;
  geojson?: GeoJSONFeatureCollection;
  bbox?: string | [number, number, number, number];
  image?: string;
  area?: string;
  category?: string;
  group?: string;
  chart?: ChartData;
  [key: string]: unknown;
}

// === Map view types ===

export interface AnalysisResult {
  center: [number, number];
  zoom: number;
}

// === Layer source types ===

export interface HeatmapRasterSource {
  image: string;
  bbox: [number, number, number, number];
}

// === Chart types (shared between panel/chart-renderer and chat/chart-renderer) ===

export interface ChartDataPoint {
  name: string;
  value?: number;
  x?: number;
  y?: number;
}

export interface ChartData {
  type: 'bar' | 'line' | 'pie' | 'scatter';
  title: string;
  data: ChartDataPoint[];
  x_label?: string;
  y_label?: string;
}

// === Map action types ===

export interface MapActionPayload {
  command: 'add_layer' | 'remove_layer' | 'fly_to' | 'add_heatmap_raster' | 'BASE_LAYER_CHANGE' | 'LAYER_VISIBILITY_UPDATE' | 'LAYER_STYLE_UPDATE' | 'REMOVE_LAYER';
  params: {
    layerId?: string;
    layer_id?: string; // Support for snake_case from backend
    name?: string;     // For base layer change
    type?: 'fill' | 'line' | 'circle' | 'symbol';
    geojson?: GeoJSONFeatureCollection;
    style?: Record<string, unknown>;
    flyTo?: boolean;
    center?: [number, number];
    zoom?: number;
    image?: string;
    bbox?: [number, number, number, number];
    opacity?: number;
    visible?: boolean;
  };
}

// === Recharts tooltip types ===

export interface RechartsTooltipItem {
  color?: string;
  fill?: string;
  name: string;
  value: number;
  payload: ChartDataPoint;
}

export interface RechartsTooltipProps {
  active?: boolean;
  payload?: RechartsTooltipItem[];
  label?: string;
}
