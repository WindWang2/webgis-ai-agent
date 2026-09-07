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
  id?: string | number;
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
  /** V4 box_plot 五数扩展（value=median）。 */
  q1?: number;
  q3?: number;
  min?: number;
  max?: number;
}

/**
 * V4 多序列（grouped/stacked bar、radar、heat_matrix 行）。
 * 每个 series 携带自己的点列；data 仍是单序列形态（向后兼容）。
 */
export interface ChartSeries {
  name: string;
  data: ChartDataPoint[];
}

/**
 * V4 图表 kind 词表：与后端 app/lib/cartography/chart_kinds.py 同源
 * （component-catalog.generated.json 的 chartKinds 段导出）。
 * violin（planned）不在联合内 —— 未实现的类型不可伪造。
 */
export type ChartKind =
  | 'bar' | 'horizontal_bar' | 'grouped_bar' | 'stacked_bar'
  | 'line' | 'area' | 'scatter' | 'histogram' | 'box_plot'
  | 'pie' | 'donut' | 'radar' | 'rose' | 'timeseries' | 'cumulative'
  | 'heat_matrix' | 'kpi_card' | 'ranking_list';

export interface ChartData {
  type: ChartKind;
  title: string;
  data: ChartDataPoint[];
  series?: ChartSeries[];
  /** stacked_bar 的堆叠语义开关（grouped_bar 缺省并排）。 */
  stacked?: boolean;
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
  command: 'add_layer' | 'remove_layer' | 'fly_to' | 'add_heatmap_raster' | 'add_raster_layer' | 'add_native_heatmap' | 'create_thematic_map' | 'APPLY_LAYER_FILTER' | 'export_map' | 'BASE_LAYER_CHANGE' | 'LAYER_VISIBILITY_UPDATE' | 'LAYER_STYLE_UPDATE' | 'REMOVE_LAYER' | 'zoom_to_bbox' | 'set_map_view' | 'REORDER_LAYER' | 'draw_measurement' | 'add_marker' | 'clear_annotations' | 'cartographic_runtime_repair' | 'query_features' | 'FINALIZE_DISPLAY' | 'MAP_FINALIZATION' | 'chart_set_state' | 'chart_move' | 'chart_resize' | 'chart_switch_type' | 'chart_highlight' | 'chart_close' | 'chart_restore' | 'chart_collapse' | 'chart_expand';
  action_id?: string;
  correlation?: MapActionCorrelation;
  issued_at?: string;
  params: {
    id?: string;
    componentId?: string;  // V4 chart_* 命令：目标 chart_panel 组件 id
    state?: string;        // V4 chart_set_state：目标状态
    chartType?: string;    // V4 chart_switch_type：目标图表 kind
    categories?: string[]; // V4 chart_highlight：联动高亮类别
    anchor?: string;       // V4 chart_move：锚点槽位
    width?: number;        // V4 chart_resize
    height?: number;       // V4 chart_resize
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
    /** MAP_FINALIZATION：完成态载荷（status + result bbox）。 */
    status?: string;
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
