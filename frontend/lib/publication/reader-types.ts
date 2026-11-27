/**
 * 只读地图发布（04 线）——读者端类型（与后端 publication_snapshot.v1 对齐）。
 * 读者面唯一事实源：`/api/v1/publications/shared/{code}` 系列端点的授权投影。
 */

export interface ReaderLayerInfo {
  layer_id: string;
  title: string;
  field_whitelist: string[];
  feature_count: number;
  reader_visibility_allowed: boolean;
}

export interface ReaderBlocker {
  reason: string;
  message: string;
  layer_id?: string | null;
  source_id?: string | null;
}

export interface ReaderMeta {
  title: string;
  purpose: string;
  attribution: string;
  license: string;
  missing_values_note: string;
}

export interface ReaderScope {
  crs: string;
  bbox: [number, number, number, number] | null;
  time_start: string | null;
  time_end: string | null;
  geometry_precision: number;
}

export interface ReaderAccessibility {
  version?: number;
  altText?: string;
  title?: string;
  layerLabels?: string[];
  colorblindSafety?: {
    palette?: string;
    context?: string;
    declaration?: string;
    checked?: boolean;
    separable?: boolean;
    minMetric?: number;
  };
  dataSources?: { name: string; attribution: string }[];
  projection?: string;
  missing?: string[];
  complete?: boolean;
}

export interface ReaderQaCheck {
  0: string;
  1: string;
}

export interface ReaderDoc {
  spec_version: string;
  product_id: string;
  version: number;
  snapshot_fingerprint: string;
  meta: ReaderMeta;
  scope: ReaderScope;
  layers: ReaderLayerInfo[];
  blockers: ReaderBlocker[];
  accessibility: ReaderAccessibility;
  qa: {
    spec_validity: string;
    compile_ok: boolean | null;
    checks: [string, string][];
  };
}

export interface ReaderDegradation {
  kind: string;
  source_id?: string;
  layer_id?: string;
  message: string;
}

export interface ReaderMapPayload {
  spec: Record<string, unknown>;
  degradations: ReaderDegradation[];
  snapshot_fingerprint: string;
}

/** 读者侧 typed 筛选谓词（白名单字段；与 MapLibre 表达式/服务端 where AST 双向映射）。 */
export interface ReaderPredicate {
  field: string;
  op: 'eq' | 'ne' | 'gte' | 'lte' | 'gt' | 'lt' | 'contains';
  value: string | number | boolean | null;
}

export interface ReaderDenominator {
  total_features: number;
  matching_features: number | null;
  numeric_count?: number;
  null_or_non_numeric?: number;
  bbox: [number, number, number, number] | null;
  where: unknown;
  note?: string;
}

export interface ReaderFeaturePage {
  features: {
    type: 'Feature';
    id?: string | number;
    properties: Record<string, unknown>;
    geometry?: unknown;
  }[];
  cursor?: string | null;
  truncated?: boolean;
  is_complete?: boolean;
  denominator?: ReaderDenominator;
}

export interface ReaderSummary {
  layer_id: string;
  field: string;
  stats: { count: number; sum: number | null; min: number | null; max: number | null; null_count: number };
  denominator: ReaderDenominator;
}
