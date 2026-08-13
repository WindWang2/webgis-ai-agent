import type { GeoJSONFeatureCollection, HeatmapRasterSource } from '../types';
import type { LegendSpec } from '@/lib/map-kit/types';

export interface LayerStyle {
  color?: string;
  strokeColor?: string;
  strokeWidth?: number;
  fill?: boolean;
  renderType?: 'heatmap' | 'grid' | 'vector';
  palette?: string;
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
}

export interface Layer {
  id: string;
  name: string;
  type: 'vector' | 'raster' | 'tile' | 'heatmap';
  visible: boolean;
  opacity: number;
  group?: 'analysis' | 'base' | 'reference';
  source?: string | GeoJSONFeatureCollection | HeatmapRasterSource;
  style?: LayerStyle;
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
