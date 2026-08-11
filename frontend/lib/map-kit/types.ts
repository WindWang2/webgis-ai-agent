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
// ADR-0052: legend_spec is the canonical thematic style — the single source
// both the live MapSpec paint (thematic-paint.ts) and <ThematicLegend> derive
// from. The optional fields below (method/labels/nodata/title) are additive and
// produced by the canonical builders; legacy payloads omit them cleanly.

export type LegendCategoryEntry = { key: string; color: string; label: string };
export type NoDataRule = { color: string; label: string };

export type GraduatedLegendSpec = {
  type: 'graduated';
  field: string;
  breaks: number[];
  palette: string;
  palette_colors: string[];
  method?: string;        // classification method (quantiles/equal_interval/natural_breaks)
  labels?: string[];      // per-class labels (count == breaks.length - 1)
  nodata?: NoDataRule;    // no-data rule (null/missing → nodata color on the live map)
  unit?: string;
  title?: string;
  format?: 'number' | 'percent' | 'currency';
};

export type ContinuousLegendSpec = {
  type: 'continuous';
  field?: string;
  min: number;
  max: number;
  palette: string;
  palette_colors: string[];
  nodata?: NoDataRule;
  unit?: string;
};

export type CategoricalLegendSpec = {
  type: 'categorical';
  field: string;
  categories: LegendCategoryEntry[];
  palette?: string;
  nodata?: NoDataRule;
  title?: string;
};

export type DivergentLegendSpec = {
  type: 'divergent';
  field?: string;
  center: number;
  min: number;
  max: number;
  palette: string;
  palette_colors: string[];
  nodata?: NoDataRule;
  unit?: string;
};

export type LegendSpec =
  | GraduatedLegendSpec
  | ContinuousLegendSpec
  | CategoricalLegendSpec
  | DivergentLegendSpec;
