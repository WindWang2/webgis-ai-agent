/**
 * MapSpec TS 类型入口（V6, ADR-0120 W3）。
 *
 * 契约脊柱（文档/源/图层/布局/帧）的唯一权威是
 * app/lib/cartography/mapspec_schema.py（Pydantic），其 TypeScript 投影
 * 在 ./types.generated.ts（AUTO-GENERATED，DO NOT EDIT）—— 本文件不再
 * 手维护平行 schema，只 re-export 并保留**编译运行时**类型（编译器
 * 报告/图例摘要等，它们不是 MapSpec 契约的一部分）。
 */
export * from './types.generated';

export interface SpatialFieldProfile {
  type: "string" | "number" | "boolean" | "date";
  min?: number;
  max?: number;
  mean?: number;
  sampleValues?: any[];
}

export interface SpatialMetaProfile {
  bbox?: [number, number, number, number];
  crs?: string;
  featureCount?: number;
  geometryTypes?: string[];
  fields?: Record<string, SpatialFieldProfile>;
  suggestedView?: { center: [number, number]; zoom: number };
}

export interface CompileError {
  code: string;
  message: string;
  layerId?: string;
  field?: string;
}

export interface CompileReport {
  success: boolean;
  errors: CompileError[];
  warnings: string[];
  stats: {
    sourceCount: number;
    layerCount: number;
    compiledLayerCount: number;
    labelLayerCount: number;
  };
}

export interface LegendItem {
  label: string;
  color: string;
  size?: number;
  type: "point" | "line" | "polygon" | "gradient";
}

export interface LegendDef {
  layerId: string;
  title: string;
  items: LegendItem[];
}

export interface MapSpecCompileResult {
  style: any;
  html: string;
  legend: LegendDef[];
  report: CompileReport;
}
