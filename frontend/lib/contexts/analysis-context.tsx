'use client';

import {
  createContext,
  useContext,
  useState,
  useCallback,
  type ReactNode,
  type Dispatch,
  type SetStateAction,
} from 'react';

/**
 * 分析结果数据类型
 */
export interface GeoJSONFeature {
  type: 'Feature';
  geometry: {
    type: 'Point' | 'LineString' | 'Polygon' | 'MultiPoint' | 'MultiLineString' | 'MultiPolygon';
    coordinates: number[] | number[][] | number[][][];
  };
  properties?: Record<string, unknown>;
}

export interface GeoJSONData {
  type: 'FeatureCollection';
  features: GeoJSONFeature[];
}

/**
 * 图层样式配置
 */
export interface LayerStyle {
  id: string;
  name: string;
  type: 'circle' | 'fill' | 'line';
  color: string;
  radius?: number;         // circle 专用
  fillOpacity?: number;    // fill 专用
  lineWidth?: number;      // line 专用
  visible: boolean;
}

/**
 * 单个分析结果项
 */
export interface AnalysisResultItem {
  id: string;
  title: string;
  type: 'text' | 'chart' | 'map';
  content: string;
  geoJson?: GeoJSONData;
  layerStyles?: LayerStyle[];
  timestamp: number;
  draft?: boolean;  // true = intermediate result, renders at 0.5 opacity
}

/**
 * 分析报告
 */
export interface AnalysisReport {
  title: string;
  summary: string;
  details: string;
  chartData?: unknown;
}

/**
 * 全局分析状态
 */
export interface AnalysisState {
  results: AnalysisResultItem[];
  report: AnalysisReport | null;
  currentLayerId: string | null;
  isAnalyzing: boolean;
}

// 默认状态
const defaultAnalysisState: AnalysisState = {
  results: [],
  report: null,
  currentLayerId: null,
  isAnalyzing: false,
};

// Context 类型
interface AnalysisContextValue {
  state: AnalysisState;
  setState: Dispatch<SetStateAction<AnalysisState>>;
  // Actions
  addResult: (result: Omit<AnalysisResultItem, 'id' | 'timestamp'>) => void;
  clearResults: () => void;
  setReport: (report: AnalysisReport | null) => void;
  setCurrentLayer: (layerId: string | null) => void;
  setAnalyzing: (analyzing: boolean) => void;
  promoteDraftLayers: () => void;
}

// 创建 Context
const AnalysisContext = createContext<AnalysisContextValue | null>(null);

/**
 * Provider 组件 - 提供全局状态
 */
export function AnalysisProvider({ children }: { children: ReactNode }) {
  const [state, setState] = useState<AnalysisState>(defaultAnalysisState);

  const addResult = useCallback(
    (result: Omit<AnalysisResultItem, 'id' | 'timestamp'>) => {
      const newResult: AnalysisResultItem = {
        ...result,
        id: `result-${Date.now()}-${Math.random().toString(36).substring(2, 9)}`,
        timestamp: Date.now(),
      };
      
      setState(prev => ({
        ...prev,
        results: [newResult, ...prev.results],
        // 如果有新图层，自动选中
        currentLayerId: newResult.layerStyles?.[0]?.id || prev.currentLayerId,
      }));
    },
    []
  );

  const clearResults = useCallback(() => {
    setState(prev => ({
      ...prev,
      results: [],
      report: null,
      currentLayerId: null,
    }));
  }, []);

  const setReport = useCallback((report: AnalysisReport | null) => {
    setState(prev => ({ ...prev, report }));
  }, []);

  const setCurrentLayer = useCallback((layerId: string | null) => {
    setState(prev => ({ ...prev, currentLayerId: layerId }));
  }, []);

  const setAnalyzing = useCallback((analyzing: boolean) => {
    setState(prev => ({ ...prev, isAnalyzing: analyzing }));
  }, []);

  const promoteDraftLayers = useCallback(() => {
    setState(prev => ({
      ...prev,
      results: prev.results.map(r => r.draft ? { ...r, draft: false } : r),
    }));
  }, []);

  const value: AnalysisContextValue = {
    state,
    setState,
    addResult,
    clearResults,
    setReport,
    setCurrentLayer,
    setAnalyzing,
    promoteDraftLayers,
  };

  return (
    <AnalysisContext.Provider value={value}>
      {children}
    </AnalysisContext.Provider>
  );
}

/**
 * 使用分析 Context 的 Hook
 */
export function useAnalysis() {
  const context = useContext(AnalysisContext);
  
  if (!context) {
    throw new Error('useAnalysis must be used within AnalysisProvider');
  }
  
  return context;
}

// ============ 结果解析辅助函数 ============

/**
 * 从 AI 响应中解析结构化数据
 * 识别 Markdown 代码块中的 JSON/GeoJSON 数据
 */
export function parseStructuredResults(aiResponse: string): {
  text: string;
  geoJson?: GeoJSONData;
  report?: Partial<AnalysisReport>;
} {
  let text = aiResponse;
  let geoJson: GeoJSONData | undefined;
  let report: Partial<AnalysisReport> | undefined;

  // 尝试解析 GeoJSON 代码块
  const geoJsonMatch = aiResponse.match(/```(?:geojson|json)\s*([\s\S]*?)```/i);
  if (geoJsonMatch) {
    try {
      const parsed = JSON.parse(geoJsonMatch[1]);
      if (parsed.type === 'FeatureCollection') {
        geoJson = parsed;
        // 从响应中移除 GeoJSON 部分，只保留文字说明
        text = aiResponse.replace(geoJsonMatch[0], '').trim();
      }
    } catch {
      // 解析失败，忽略
    }
  }

  // 尝试解析报告数据
  const reportMatch = aiResponse.match(/```report\s*([\s\S]*?)```/i);
  if (reportMatch) {
    try {
      report = JSON.parse(reportMatch[1]);
      text = text.replace(reportMatch[0], '').trim();
    } catch {
      // 解析失败，忽略
    }
  }

  return { text, geoJson, report };
}

/**
 * 生成默认图层样式
 */
export function generateDefaultLayerStyle(
  resultId: string,
  geoJson?: GeoJSONData
): LayerStyle[] {
  if (!geoJson || geoJson.features.length === 0) {
    return [];
  }

  const colors = [
    '#3b82f6', // blue
    '#10b981', // emerald
    '#f59e0b', // amber
    '#ef4444', // red
    '#8b5cf6', // violet
  ];

  return [{
    id: `layer-${resultId}`,
    name: `分析结果 ${resultId.slice(-4)}`,
    type: 'circle',
    color: colors[0],
    radius: 8,
    visible: true,
  }];
}