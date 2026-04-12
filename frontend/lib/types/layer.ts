import type { GeoJSONFeatureCollection, HeatmapRasterSource } from '../types';

export interface LayerStyle {
  color?: string;
  renderType?: 'heatmap' | 'grid';
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
  created_at?: string;
  updated_at?: string;
}

export type SortField = 'name' | 'created_at' | 'updated_at';

export interface SortOption {
  field: SortField;
  order: 'asc' | 'desc';
}
