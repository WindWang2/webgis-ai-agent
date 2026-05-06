# GeoAgent v2 前端重设计实现方案

**日期：** 2026-05-06
**版本：** v2.0
**状态：** 待实现

## 概述

基于 `WebGIS AI Agent v2.html` 设计文件，完整重构 GeoAgent 前端界面，实现所有新功能组件。

## 设计决策

### 1. 实现范围
- **完整实现所有新功能**：AgentEnvHUD、独立 RAG 面板、导出功能、操作日志、浮动图例、底图切换器、Tweaks 面板
- **RAG 后端暂不改动**：前端使用模拟数据展示
- **组件结构重构**：按新设计调整文件组织
- **样式方案**：视觉一致性优先，按设计文件使用内联样式
- **测试策略**：完整的 UI 测试

### 2. 实现策略
采用**渐进式重构**，分四个阶段实现：
1. 组件结构搭建
2. 核心组件重构
3. 新功能组件实现
4. 集成与测试

## 架构

### 组件结构

```
frontend/components/
├── layout/
│   ├── top-bar.tsx          (更新)
│   └── status-bar.tsx       (更新)
├── sidebar/
│   ├── left-sidebar.tsx     (重构: 增加标签页)
│   ├── chat-tab.tsx         (更新: 增加工具调用展示)
│   ├── layers-tab.tsx       (保持)
│   ├── ops-log-tab.tsx      (新增: 操作日志)
│   ├── exports-tab.tsx      (新增: 导出列表)
│   └── rag-tab.tsx          (新增: RAG 结果标签页)
├── map/
│   ├── map-toolbar.tsx      (重构: 增加 2D/3D、HUD 开关)
│   ├── baselayer-switcher.tsx (新增: 底图切换)
│   └── floating-legend.tsx  (新增: 浮动图例)
├── hud/
│   ├── agent-env-hud.tsx    (新增: Agent 环境感知面板)
│   └── causal-trace.tsx     (新增: 因果链追踪)
├── panel/
│   ├── rag-independent-panel.tsx (新增: 独立 RAG 面板)
│   ├── settings-panel.tsx   (重构: 增加 tweaks)
│   └── history-drawer.tsx   (保持)
├── overlays/
│   ├── ai-tracker.tsx       (重构)
│   └── perception-rings.tsx (新增: 感知环动画)
└── tweaks-panel.tsx         (新增: 调整面板)
```

### 状态管理扩展

在 `useHudStore` 中新增：

```typescript
// 面板开关
hudOpen: boolean;
setHudOpen: (v: boolean) => void;
ragPanelOpen: boolean;
setRagPanelOpen: (v: boolean) => void;
tweaksOpen: boolean;
setTweaksOpen: (v: boolean) => void;

// UI 调整
accentColor: string;
setAccentColor: (v: string) => void;
fontSize: number;
setFontSize: (v: number) => void;
density: 'compact' | 'comfortable';
setDensity: (v: 'compact' | 'comfortable') => void;
showGrid: boolean;
setShowGrid: (v: boolean) => void;
sidebarWidth: number;
setSidebarWidth: (v: number) => void;

// 新功能数据
opsLog: OpLogEntry[];
pushOpLog: (entry: OpLogEntry) => void;
clearOpsLog: () => void;
ragResults: RagResult[];
setRagResults: (results: RagResult[]) => void;
exports: ExportItem[];
setExports: (items: ExportItem[]) => void;
causalChain: CausalEntry[];
pushCausalEntry: (entry: CausalEntry) => void;
clearCausalChain: () => void;

// 模拟数据模式
demoMode: boolean;
```

## 数据模型

### 新增类型定义

```typescript
// 操作日志
interface OpLogEntry {
  id: string;
  type: 'add' | 'remove' | 'toggle' | 'flyto' | 'style';
  label: string;
  time: string;
  detail?: string;
}

// RAG 检索结果
interface RagResult {
  id: string;
  source: string;
  score: string;
  chunks: number;
  excerpts: string[];
}

// 导出项
interface ExportItem {
  id: string;
  name: string;
  type: 'png' | 'pdf' | 'geojson';
  size: string;
  date: string;
}

// 因果链
interface CausalEntry {
  id: string;
  tool: string;
  mapAction?: string;
  time: string;
  toolInput?: string;
  mapEffect?: string;
  mapState?: Record<string, unknown>;
}

// LeftTab 扩展
type LeftTab = 'chat' | 'layers' | 'ops' | 'exports';
```

## 实现阶段

### 阶段一：组件结构搭建
- [ ] 创建新的组件文件结构
- [ ] 定义类型扩展 (`hud-types.ts`)
- [ ] 更新 `useHudStore` 状态
- [ ] 写组件基础框架（空壳）

### 阶段二：核心组件重构
- [ ] 更新 TopBar
- [ ] 更新 StatusBar
- [ ] 重构 LeftSidebar（多标签页支持）
- [ ] 重构 MapToolbar（新增功能）

### 阶段三：新功能组件实现
- [ ] AgentEnvHUD 面板
- [ ] RagIndependentPanel
- [ ] BaselayerSwitcher
- [ ] FloatingLegend
- [ ] PerceptionRings（感知环动画）
- [ ] TweaksPanel
- [ ] OpsLogTab
- [ ] ExportsTab
- [ ] CausalTrace

### 阶段四：集成与测试
- [ ] 整合所有组件到 `page.tsx`
- [ ] 添加模拟数据（DEMO 数据）
- [ ] UI 测试
- [ ] 样式微调

## 模拟数据

### DEMO_MESSAGES
```typescript
const DEMO_MESSAGES = [
  {
    id: '1',
    role: 'assistant',
    content: '你好！我是 GeoAgent。\n\n我感知地图、分析空间、生成洞察——地图上的一切都是我的一部分。\n\n试着告诉我：\n- 分析北京市学校分布密度\n- 成都市人口热力图\n- 计算各区 POI 覆盖率',
    timestamp: '14:30',
  },
];
```

### DEMO_LAYERS
```typescript
const DEMO_LAYERS = [
  { id: 'poi-schools', name: '北京市学校 POI', type: 'vector', visible: true, color: '#16a34a', group: 'analysis', info: '312 个要素 · query_osm_poi', mockPoints: [[22,18],[28,22],[35,28],[42,15],[50,32],[55,25],[60,18],[38,42],[46,38],[62,45]] },
  { id: 'heatmap-density', name: '密度热力图', type: 'heatmap', visible: true, color: '#ff5f00', group: 'analysis', info: '核密度估计 · kde_surface', mockPoints: [[30,25],[35,30],[40,28],[38,22],[33,20],[45,35],[50,28],[44,22]] },
  { id: 'boundary-districts', name: '北京市行政区划', type: 'vector', visible: false, color: '#2563eb', group: 'reference', info: '16 个区 · get_district', mockPoints: [] },
];
```

### DEMO_RAG
```typescript
const DEMO_RAG = [
  { source: 'GIS空间分析方法论.pdf', score: '0.91', chunks: 3, excerpts: ['核密度估计（KDE）是一种非参数方法，用于估计随机变量的概率密度函数。在 GIS 中，常用于分析点要素的空间分布密度...','带宽选择是 KDE 的关键参数，过小会造成过拟合，过大则会掩盖局部模式。常用的带宽选择方法包括 Silverman 规则...'] },
  { source: '北京市空间数据手册v3.md', score: '0.87', chunks: 2, excerpts: ['北京市共辖 16 个区，总面积 16410 平方公里。核心区包括东城区和西城区...','2023年北京市常住人口 2185 万人，其中城镇人口 1891 万人，城镇化率为 86.6%...'] },
  { source: 'OpenStreetMap POI 分类标准.pdf', score: '0.79', chunks: 1, excerpts: ['教育类 POI 在 OSM 中使用 amenity=school/university/college 标签进行标注...'] },
];
```

### DEMO_EXPORTS
```typescript
const DEMO_EXPORTS = [
  { name: '北京学校密度专题图', type: 'png', size: '2.4 MB', date: '今天 14:35' },
  { name: '核密度分析报告', type: 'pdf', size: '840 KB', date: '今天 14:33' },
  { name: '学校POI数据', type: 'geojson', size: '156 KB', date: '今天 14:30' },
  { name: '行政区划叠加图', type: 'png', size: '1.8 MB', date: '昨天' },
];
```

### DEMO_OPS_LOG
```typescript
const DEMO_OPS_LOG = [
  { type: 'add', label: '添加图层 — 北京市学校 POI', time: '14:30', detail: '312 个点要素' },
  { type: 'flyto', label: '飞到 — 北京市中心', time: '14:31', detail: 'zoom 11.5 / [116.40, 39.90]' },
  { type: 'add', label: '添加图层 — 密度热力图', time: '14:32', detail: 'kde_surface 输出' },
  { type: 'style', label: '样式变更 — 热力图不透明度', time: '14:33', detail: '0.85 → 0.70' },
  { type: 'toggle', label: '隐藏图层 — 北京市行政区划', time: '14:34', detail: '切换为不可见' },
];
```

### DEMO_CAUSAL_CHAIN
```typescript
const DEMO_CAUSAL_CHAIN = [
  { tool: 'geocode_cn', mapAction: 'fly_to', time: '14:30', toolInput: '北京市', mapEffect: '地图飞至 [116.40, 39.90] zoom 10', mapState: { center: [116.40,39.90], zoom: 10 } },
  { tool: 'query_osm_poi', mapAction: 'add_layer', time: '14:31', toolInput: 'category=school, city=北京市', mapEffect: '新增 "北京市学校 POI" 图层（312 要素）', mapState: { layer_id: 'poi-schools', feature_count: 312 } },
  { tool: 'kde_surface', mapAction: 'add_layer', time: '14:32', toolInput: 'layer_id=poi-schools, bandwidth=500m', mapEffect: '新增 "密度热力图" 图层', mapState: { layer_id: 'heatmap-density', render_type: 'native_heatmap' } },
];
```

## 注意事项

1. **样式优先级**：按设计文件使用内联样式，保持视觉一致性
2. **RAG 后端**：暂时不改动，前端使用模拟数据
3. **现有功能**：保持 chat、session、websocket 等现有功能
4. **测试**：每个组件完成后都要进行 UI 测试
5. **Git**：每个阶段完成后提交一次

## 下一步

此设计文档批准后，调用 `writing-plans` 技能生成详细的实现计划。
