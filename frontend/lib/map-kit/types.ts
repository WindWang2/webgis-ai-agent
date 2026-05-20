export interface ViewportParams {
  center: [number, number];
  zoom: number;
  bearing?: number;
  pitch?: number;
}

export interface MapKitOptions {
  defaultDuration?: number;
  defaultPadding?: number;
}

export interface ThematicStyleDef {
  type: 'choropleth' | 'lisa';
  field: string;
  breaks?: number[];
  colors?: string[];
  palette?: string[];
  legend_labels?: string[];
  categories?: Record<string, string>;
  geometry_type?: 'Polygon' | 'Point';
}

export interface GeoAnalysisResult {
  success: boolean;
  data: any;
  summary: string;
}

// ─── Legend Spec contract (backend → frontend) ───────────────────────────────

export type LegendCategoryEntry = { key: string; color: string; label: string };

export type GraduatedLegendSpec = {
  type: 'graduated';
  field: string;
  breaks: number[];
  palette: string;
  palette_colors: string[];
  unit?: string;
  format?: 'number' | 'percent' | 'currency';
};

export type ContinuousLegendSpec = {
  type: 'continuous';
  field?: string;
  min: number;
  max: number;
  palette: string;
  palette_colors: string[];
};

export type CategoricalLegendSpec = {
  type: 'categorical';
  field: string;
  categories: LegendCategoryEntry[];
};

export type DivergentLegendSpec = {
  type: 'divergent';
  field?: string;
  center: number;
  min: number;
  max: number;
  palette: string;
  palette_colors: string[];
};

export type LegendSpec =
  | GraduatedLegendSpec
  | ContinuousLegendSpec
  | CategoricalLegendSpec
  | DivergentLegendSpec;
