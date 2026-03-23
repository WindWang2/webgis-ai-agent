import { Circle, Layers, Grid3x3, BarChart3, Route, FileSpreadsheet } from 'lucide-react';

export interface AnalysisType {
  id: string;
  name: string;
  description: string;
  icon: React.ComponentType<{ className?: string }>;
}

export const ANALYSIS_TYPES: AnalysisType[] = [
  {
    id: 'buffer',
    name: '缓冲区分析',
    description: '创建要素周围的缓冲区区域',
    icon: Circle,
  },
  {
    id: 'intersect',
    name: '叠加分析',
    description: '计算图层交集',
    icon: Layers,
  },
  {
    id: 'union',
    name: '合并分析',
    description: '合并图层所有要素',
    icon: Layers,
  },
  {
    id: 'erase',
    name: '擦除分析',
    description: '从一个图层中擦除另一个图层',
    icon: Layers,
  },
  {
    id: 'grid',
    name: '网格分析',
    description: '创建规则网格并统计',
    icon: Grid3x3,
  },
  {
    id: 'classify',
    name: '分类渲染',
    description: '按属性值分类显示',
    icon: BarChart3,
  },
  {
    id: 'network',
    name: '网络分析',
    description: '路径规划与可达性分析',
    icon: Route,
  },
  {
    id: 'statistics',
    name: '统计分析',
    description: '空间统计与汇总',
    icon: FileSpreadsheet,
  },
];

// Task interface for progress tracking
export interface Task {
  id: string;
  type: string;
  status: 'queued' | 'processing' | 'completed' | 'failed';
  progress: number;
  createdAt: string;
  completedAt?: string;
  errorMessage?: string;
  resultLayerId?: string;
  params: Record<string, unknown>;
}
