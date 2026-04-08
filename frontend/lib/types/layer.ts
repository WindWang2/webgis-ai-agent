export interface Layer {
  id: string;
  name: string;
  type: 'vector' | 'raster' | 'tile';
  visible: boolean;
  opacity: number;
  source?: string | Record<string, any>;
  style?: Record<string, any>;
  created_at?: string;
  updated_at?: string;
}

export type SortField = 'name' | 'created_at' | 'updated_at';

export interface SortOption {
  field: SortField;
  order: 'asc' | 'desc';
}
