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

/**
 * V3 interaction-evidence correlation (Harness–Map Interaction Closed Loop).
 * All fields optional so legacy producers (text-JSON path, demo mode) keep working.
 * `action_id` is minted backend-side (`ma-…`) inside each command dict; the
 * frontend falls back to a client id (`fe-…`) for locally synthesized actions.
 */
export interface MapActionCorrelation {
  session_id?: string;
  run_id?: string;
  turn_id?: string;
  task_id?: string;
  step_id?: string;      // = tool_call_id on the Pi path
  sse_event_id?: string; // per-turn monotonic SSE event id (Last-Event-ID space)
}

/** Terminal lifecycle states of a map action (queued/running are transient). */
export type MapActionTerminalStatus = 'succeeded' | 'failed' | 'cancelled' | 'superseded';

export interface MapActionPayload {
  command: 'add_layer' | 'remove_layer' | 'fly_to' | 'add_heatmap_raster' | 'add_raster_layer' | 'add_native_heatmap' | 'create_thematic_map' | 'APPLY_LAYER_FILTER' | 'export_map' | 'BASE_LAYER_CHANGE' | 'LAYER_VISIBILITY_UPDATE' | 'LAYER_STYLE_UPDATE' | 'REMOVE_LAYER' | 'zoom_to_bbox' | 'set_map_view' | 'REORDER_LAYER' | 'draw_measurement' | 'add_marker' | 'clear_annotations' | 'cartographic_runtime_repair' | 'query_features' | 'FINALIZE_DISPLAY';
  action_id?: string;
  correlation?: MapActionCorrelation;
  issued_at?: string;
  params: {
    id?: string;
    layerId?: string;
    layer_id?: string; // Support for snake_case from backend
    name?: string;     // For base layer change
    type?: 'fill' | 'line' | 'circle' | 'symbol';
    geojson?: GeoJSONFeatureCollection;
    filter?: any;      // Filter for APPLY_LAYER_FILTER
    palette?: string;  // Palette for add_native_heatmap
    radius?: number;   // Radius for add_native_heatmap
    style?: Record<string, unknown>;
    flyTo?: boolean;
    center?: [number, number];
    zoom?: number;
    bearing?: number;
    pitch?: number;
    image?: string;
    url?: string;
    bbox?: [number, number, number, number];
    location?: [number, number]; // For query_features [lng, lat]
    buffer_m?: number;           // For query_features query radius (meters)
    opacity?: number;
    visible?: boolean;
    title?: string;
    subtitle?: string;
    showWatermark?: boolean;
    showLegend?: boolean;
    showCompass?: boolean;
    showScale?: boolean;
    showMetadata?: boolean;
    showGraticules?: boolean;
    author?: string;
    dataSource?: string;
    include_legend?: boolean;
    include_compass?: boolean;
    include_scale?: boolean;
    dark_mode?: boolean;
    format?: string;
    paperSize?: 'screen' | 'A4' | 'A3';
    orientation?: 'landscape' | 'portrait';
    dpi?: number;
    padding?: number;  // For zoom_to_bbox
    position?: string; // For REORDER_LAYER
    before_id?: string; // For REORDER_LAYER position=before
    // R8 annotation
    shape?: 'polyline' | 'polygon';
    coordinates?: number[][];
    label?: string | null;
    longitude?: number;
    latitude?: number;
    color?: string;  // R8 add_marker pin color (hex)
    mapspec_fingerprint?: string;
    observation_sequence?: number;
    patch_fingerprint?: string;
    repair_patches?: Array<{
      layer_id: string;
      mapspec_layer_id: string;
      before: Record<string, unknown>;
      desired: Record<string, unknown>;
      rules: string[];
    }>;
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
