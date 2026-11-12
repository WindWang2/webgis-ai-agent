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
  type: 'choropleth' | 'lisa' | 'categorical';
  field: string;
  breaks?: number[];
  colors?: string[];
  palette?: string[];
  legend_labels?: string[];
  /** lisa：{value: color}；categorical：#557 为 [{key,color,label}] 列表（保留键类型）。 */
  categories?: Record<string, string> | Array<{ key: string | number; color: string; label?: string }>;
  geometry_type?: 'Polygon' | 'Point';
}

export interface GeoAnalysisResult {
  success: boolean;
  data: any;
  summary: string;
}

// ─── Legend Spec contract (backend → frontend) ───────────────────────────────
// ADR-0078: legend_spec is the canonical thematic style — the single source
// both the live MapSpec paint (thematic-paint.ts) and <ThematicLegend> derive
// from. The optional fields below (method/labels/nodata/title) are additive and
// produced by the canonical builders; legacy payloads omit them cleanly.

export type LegendCategoryEntry = { key: string; color: string; label: string;
  /** G33（加性）：源值原始类型（number|bool 的 match case 类型化——数值字段
   *  的字符串键在 MapLibre 严格类型 match 下从不命中）。 */
  key_type?: 'number' | 'string' | 'bool'; };
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
  /** G33（加性）：显式 other 桶（未列出值的颜色与图例文本；缺省沿用
   *  既有「末类颜色」合同，行为不变）。 */
  default?: { color: string; label?: string };
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

/** V4：双变量色阵图例（后端 bivariate.bivariate_legend_spec 同源）。 */
export type BivariateLegendSpec = {
  type: 'bivariate';
  matrix: string;
  colors: string[];
  n: number;
  label_a: string;
  label_b: string;
  breaks_a: number[];
  breaks_b: number[];
  class_field?: string;
  field?: string;
  title?: string;
};

export type LegendSpec =
  | GraduatedLegendSpec
  | ContinuousLegendSpec
  | CategoricalLegendSpec
  | DivergentLegendSpec
  | BivariateLegendSpec;
