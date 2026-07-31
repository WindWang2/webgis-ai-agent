export type StyleMethodType = "constant" | "interpolate" | "step" | "match" | "field";

export interface ConstantStyleMethod {
  method: "constant";
  value: string | number | boolean;
}

export interface InterpolateStyleMethod {
  method: "interpolate";
  field: string;
  stops: Array<[number, string | number]>;
}

export interface StepStyleMethod {
  method: "step";
  field: string;
  stops: Array<[number, string | number]>;
  default?: string | number;
}

export interface MatchStyleMethod {
  method: "match";
  field: string;
  cases: Array<[string | number, string | number]>;
  default: string | number;
}

export interface FieldStyleMethod {
  method: "field";
  field: string;
}

export type StyleMethod =
  | ConstantStyleMethod
  | InterpolateStyleMethod
  | StepStyleMethod
  | MatchStyleMethod
  | FieldStyleMethod
  | string
  | number
  | boolean;

export interface MapSpecSource {
  type: "geojson";
  dataPath?: string;
  url?: string;
  inlineData?: any;
}

export interface MapSpecLayerLabel {
  field: string;
  size?: number | StyleMethod;
  color?: string | StyleMethod;
  haloColor?: string;
  haloWidth?: number;
}

export interface MapSpecLayerPaint {
  color?: StyleMethod;
  radius?: StyleMethod;
  width?: StyleMethod;
  opacity?: StyleMethod;
  strokeColor?: StyleMethod;
  strokeWidth?: StyleMethod;
  [key: string]: StyleMethod | undefined;
}

export interface MapSpecLayerLayout {
  visibility?: "visible" | "none";
  labelField?: string;
  labelSize?: number | StyleMethod;
  labelColor?: string | StyleMethod;
  [key: string]: any;
}

export interface MapSpecLayer {
  id: string;
  source: string;
  type: "circle" | "line" | "fill" | "symbol" | "heatmap";
  paint?: MapSpecLayerPaint;
  layout?: MapSpecLayerLayout;
  label?: MapSpecLayerLabel;
}

export interface MapSpecView {
  center?: [number, number];
  zoom?: number;
  pitch?: number;
  bearing?: number;
}

export interface MapSpecLegendConfig {
  title?: string;
  position?: "top-right" | "top-left" | "bottom-right" | "bottom-left";
  visible?: boolean;
}

export interface MapSpecControlConfig {
  type: "navigation" | "scale" | "fullscreen";
  position?: "top-right" | "top-left" | "bottom-right" | "bottom-left";
}

export interface MapSpecLayoutConfig {
  legend?: MapSpecLegendConfig;
  controls?: MapSpecControlConfig[];
  margins?: { top?: number; right?: number; bottom?: number; left?: number };
}

export interface MapSpec {
  version: string;
  view?: MapSpecView;
  sources: Record<string, MapSpecSource>;
  layers: MapSpecLayer[];
  layout?: MapSpecLayoutConfig;
  thresholds?: {
    maxFeatures?: number;
    timeoutMs?: number;
  };
}

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
