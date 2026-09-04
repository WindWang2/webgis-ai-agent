import type { GeoJSONFeatureCollection, HeatmapRasterSource } from '../types';
import type { LegendSpec } from '@/lib/map-kit/types';

export interface LayerStyle {
  color?: string;
  strokeColor?: string;
  strokeWidth?: number;
  fill?: boolean;
  renderType?: 'heatmap' | 'grid' | 'vector';
  palette?: string;
  /** 显式视觉热力半径（像素）—— 后端 heatmap 契约投影（dispatch _runtime_patch）。 */
  radius_px?: number;
  /** legacy 像素语义（面板/模板值，4-100 直通）。 */
  radius?: number;
  intensity?: number;
  pointSize?: number;
  dashArray?: string;
  brightness?: number;
  contrast?: number;
  saturation?: number;
  [key: string]: unknown;
}

/** V3 Performance: Lightweight ref metadata computed once at ref creation. */
export interface RefDescriptor {
  ref_id: string;
  feature_count: number;
  point_count: number;
  geometry_types: string[];
  bbox: [number, number, number, number] | null;
  mvt_capable: boolean;
  estimated_bytes: number;
  content_hash: string | null;
  /** V5-E: monotonic per-ref content revision (bumped on overwrite/rollback). */
  content_revision?: number;
  // #668: attribute whitelist for MVT tile filtering
  filterable_fields?: string[] | null;
  raster_capable: boolean;
}

export interface Layer {
  id: string;
  name: string;
  type: 'vector' | 'raster' | 'tile' | 'heatmap' | 'fill-extrusion';
  visible: boolean;
  opacity: number;
  group?: 'analysis' | 'base' | 'reference';
  source?: string | GeoJSONFeatureCollection | HeatmapRasterSource;
  style?: LayerStyle;
  paint?: Record<string, unknown>;
  extrusion?: Record<string, unknown>;
  isoline?: Record<string, unknown>;
  /** Imperative MapLibre filter expression (APPLY_LAYER_FILTER). Persisted on the
   *  store layer so the MapSpecRuntime reconcile re-emits it (issue #393: a bare
   *  map.setFilter is rolled back by the next reconcile). null/absent = no filter. */
  filter?: unknown[] | null;
  _refId?: string;
  /** Data Plane: MVT tile URL template ({z}/{x}/{y}) for large ref layers. */
  _tileUrl?: string;
  /** V3 Performance: Pre-computed descriptor; allows MVT decision without downloading full FC. */
  _descriptor?: RefDescriptor;
  /** Backend MapSpec generation whose presentation this HUD layer reconciles. */
  _mapspecFingerprint?: string;
  /** Semantic MapSpec layer id (the HUD id remains the stable result ref). */
  _mapspecLayerId?: string;
  /** Local ordering only; never used as evidence or sent to the backend. */
  _mapspecGenerationAt?: number;
  /** Fingerprint of the bounded server-authored HUD presentation projection. */
  _mapspecProjectionFingerprint?: string;
  /** Last AUTO_SAFE repair action applied to this generation. */
  _mapspecRepairActionId?: string;
  /** Monotonic HUD mutation generation used to supersede stale repairs. */
  _intentGeneration?: number;
  /** 「地图随对话」：最后一次被展示（agent display / 用户点开）所属的对话轮次。
   *  新一轮 agent 展示时，落后轮次的可见分析图层会被收起（lib/chat/turn-focus）。 */
  _displayTurn?: number;
  /** 用户手动点开并保持 pin（finalize 收口豁免——用户优先，不与用户对抗；
   *  用户手动隐藏时解除）。见 lib/chat/turn-focus 的 tagUserDisplayed。 */
  _userPinned?: boolean;
  /** Bounded desired-state AUTO_SAFE repairs applied before runtime reconcile. */
  _cartographicRepairs?: Array<Record<string, unknown>>;
  created_at?: string;
  updated_at?: string;
  legend_spec?: LegendSpec;
}

export type SortField = 'name' | 'created_at' | 'updated_at';

export interface SortOption {
  field: SortField;
  order: 'asc' | 'desc';
}
