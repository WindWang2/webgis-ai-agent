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
