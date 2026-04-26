import type { GeoJSONFeatureCollection, HeatmapRasterSource } from '../types';

export interface LayerStyle {
  color?: string;
  strokeColor?: string;
  strokeWidth?: number;
  fill?: boolean;
  renderType?: 'heatmap' | 'grid' | 'vector';
  palette?: string;
  radius?: number;
  intensity?: number;
  [key: string]: unknown;
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
  created_at?: string;
  updated_at?: string;
}

export type SortField = 'name' | 'created_at' | 'updated_at';

export interface SortOption {
  field: SortField;
  order: 'asc' | 'desc';
}
