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
  source_field?: string;
  source_domain?: [number, number];
  transform?: 'linear' | 'sqrt' | 'log1p';
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

/**
 * G41：估计值 + 有来源不确定度的双通道 canonical 图例。
 * 内容由 app/services/bivariate_reliability_authoring/legend.py 单一产出；
 * 索引签名允许后端加性扩展字段而不破坏前端类型收敛。
 */
export type ReliabilityLegendSpec = {
  type: 'reliability_dual_channel';
  title?: string;
  unit?: string;
  entries?: Array<{ label: string; color: string; kind?: string }>;
  estimate?: { label?: string; unit?: string | null; palette_colors?: string[] };
  measure?: { kind?: string; level?: number; unit_note?: string; visual_meaning?: string; source?: string };
  buckets?: Array<{ key: string; label: string; output: number; count: number }>;
  missing?: { label?: string; count?: number; policy?: string };
  [key: string]: unknown;
};

export type LegendSpec =
  | GraduatedLegendSpec
  | ContinuousLegendSpec
  | CategoricalLegendSpec
  | DivergentLegendSpec
  | BivariateLegendSpec
  | ReliabilityLegendSpec;
