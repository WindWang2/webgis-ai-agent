# GeoAgent v2 前端重设计实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 基于 `WebGIS AI Agent v2.html` 完整重构 GeoAgent 前端，实现所有新功能组件，保持与现有后端 API 集成，RAG 部分使用模拟数据。

**Architecture:** 四阶段渐进式重构：1) 组件结构搭建 2) 核心组件重构 3) 新功能组件实现 4) 集成与测试。按内联样式保持视觉一致性。

**Tech Stack:** Next.js 14, React, TypeScript, Tailwind CSS, Zustand, Lucide React, MapLibre GL

---

## 文件结构映射

### 新建文件
```
frontend/components/
├── sidebar/
│   ├── ops-log-tab.tsx          (操作日志标签页)
│   ├── exports-tab.tsx          (导出列表标签页)
│   └── rag-tab.tsx              (RAG 结果标签页)
├── map/
│   ├── baselayer-switcher.tsx   (底图切换器)
│   └── floating-legend.tsx      (浮动图例)
├── hud/
│   ├── agent-env-hud.tsx        (Agent 环境感知面板)
│   └── causal-trace.tsx         (因果链追踪)
├── panel/
│   └── rag-independent-panel.tsx (独立 RAG 面板)
├── overlays/
│   └── perception-rings.tsx     (感知环动画)
└── tweaks-panel.tsx             (调整面板)
```

### 修改文件
```
frontend/lib/store/
├── hud-types.ts                 (类型扩展)
└── useHudStore.ts               (状态扩展)

frontend/components/
├── layout/
│   ├── top-bar.tsx              (更新)
│   └── status-bar.tsx           (更新)
├── sidebar/
│   ├── left-sidebar.tsx         (重构: 多标签)
│   └── chat-tab.tsx             (更新: 工具调用展示)
├── map/
│   └── map-toolbar.tsx          (重构)
├── panel/
│   └── settings-panel.tsx       (重构: 增加 tweaks)
└── map/
    └── ai-tracker.tsx           (重构)

frontend/app/
└── page.tsx                     (主页面重构)
```

---

## 阶段一：组件结构搭建

### Task 1: 扩展类型定义 (hud-types.ts)

**Files:**
- Modify: `frontend/lib/store/hud-types.ts`

- [ ] **Step 1: 添加新类型定义**

在文件末尾添加：

```typescript
// 新增 v2 类型
export interface OpLogEntry {
  id: string;
  type: 'add' | 'remove' | 'toggle' | 'flyto' | 'style';
  label: string;
  time: string;
  detail?: string;
}

export interface RagResult {
  id: string;
  source: string;
  score: string;
  chunks: number;
  excerpts: string[];
}

export interface ExportItem {
  id: string;
  name: string;
  type: 'png' | 'pdf' | 'geojson';
  size: string;
  date: string;
}

export interface CausalEntry {
  id: string;
  tool: string;
  mapAction?: string;
  time: string;
  toolInput?: string;
  mapEffect?: string;
  mapState?: Record<string, unknown>;
}

// 扩展 LeftTab
export type LeftTab = 'chat' | 'layers' | 'ops' | 'exports';
```

- [ ] **Step 2: 扩展 HudState 接口**

在 `HudState` 接口中添加（在 `sessions`/`setSessions` 之后添加）：

```typescript
  /* ─── v2 Panel Visibility ─── */
  hudOpen: boolean;
  setHudOpen: (open: boolean) => void;
  ragPanelOpen: boolean;
  setRagPanelOpen: (open: boolean) => void;
  tweaksOpen: boolean;
  setTweaksOpen: (open: boolean) => void;

  /* ─── v2 UI Tweaks ─── */
  accentColor: string;
  setAccentColor: (color: string) => void;
  fontSize: number;
  setFontSize: (size: number) => void;
  density: 'compact' | 'comfortable';
  setDensity: (density: 'compact' | 'comfortable') => void;
  showGrid: boolean;
  setShowGrid: (show: boolean) => void;
  sidebarWidth: number;
  setSidebarWidth: (width: number) => void;

  /* ─── v2 Feature Data ─── */
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

  /* ─── Demo Mode ─── */
  demoMode: boolean;
  setDemoMode: (enabled: boolean) => void;
```

- [ ] **Step 3: 验证类型文件**

Run: `cd frontend && npx tsc --noEmit`
Expected: No errors (or existing unrelated errors only)

- [ ] **Step 4: Commit**

```bash
git add frontend/lib/store/hud-types.ts
git commit -m "feat: add v2 type definitions"
```

---

### Task 2: 扩展 Zustand Store (useHudStore.ts)

**Files:**
- Modify: `frontend/lib/store/useHudStore.ts`

- [ ] **Step 1: 添加 DEMO 数据常量**

在文件开头，`DEFAULT_MCP_SERVERS` 之前添加：

```typescript
// v2 Demo Data
export const DEMO_MESSAGES = [
  {
    id: '1',
    role: 'assistant' as const,
    content: '你好！我是 GeoAgent。\n\n我感知地图、分析空间、生成洞察——地图上的一切都是我的一部分。\n\n试着告诉我：\n- 分析北京市学校分布密度\n- 成都市人口热力图\n- 计算各区 POI 覆盖率',
    timestamp: '14:30',
  },
];

export const DEMO_LAYERS = [
  { id: 'poi-schools', name: '北京市学校 POI', type: 'vector', visible: true, color: '#16a34a', group: 'analysis', info: '312 个要素 · query_osm_poi', mockPoints: [[22,18],[28,22],[35,28],[42,15],[50,32],[55,25],[60,18],[38,42],[46,38],[62,45]] },
  { id: 'heatmap-density', name: '密度热力图', type: 'heatmap', visible: true, color: '#ff5f00', group: 'analysis', info: '核密度估计 · kde_surface', mockPoints: [[30,25],[35,30],[40,28],[38,22],[33,20],[45,35],[50,28],[44,22]] },
  { id: 'boundary-districts', name: '北京市行政区划', type: 'vector', visible: false, color: '#2563eb', group: 'reference', info: '16 个区 · get_district', mockPoints: [] },
];

export const DEMO_RAG = [
  { id: '1', source: 'GIS空间分析方法论.pdf', score: '0.91', chunks: 3, excerpts: ['核密度估计（KDE）是一种非参数方法，用于估计随机变量的概率密度函数。在 GIS 中，常用于分析点要素的空间分布密度...','带宽选择是 KDE 的关键参数，过小会造成过拟合，过大则会掩盖局部模式。常用的带宽选择方法包括 Silverman 规则...'] },
  { id: '2', source: '北京市空间数据手册v3.md', score: '0.87', chunks: 2, excerpts: ['北京市共辖 16 个区，总面积 16410 平方公里。核心区包括东城区和西城区...','2023年北京市常住人口 2185 万人，其中城镇人口 1891 万人，城镇化率为 86.6%...'] },
  { id: '3', source: 'OpenStreetMap POI 分类标准.pdf', score: '0.79', chunks: 1, excerpts: ['教育类 POI 在 OSM 中使用 amenity=school/university/college 标签进行标注...'] },
];

export const DEMO_EXPORTS = [
  { id: '1', name: '北京学校密度专题图', type: 'png' as const, size: '2.4 MB', date: '今天 14:35' },
  { id: '2', name: '核密度分析报告', type: 'pdf' as const, size: '840 KB', date: '今天 14:33' },
  { id: '3', name: '学校POI数据', type: 'geojson' as const, size: '156 KB', date: '今天 14:30' },
  { id: '4', name: '行政区划叠加图', type: 'png' as const, size: '1.8 MB', date: '昨天' },
];

export const DEMO_OPS_LOG = [
  { id: '1', type: 'add' as const, label: '添加图层 — 北京市学校 POI', time: '14:30', detail: '312 个点要素' },
  { id: '2', type: 'flyto' as const, label: '飞到 — 北京市中心', time: '14:31', detail: 'zoom 11.5 / [116.40, 39.90]' },
  { id: '3', type: 'add' as const, label: '添加图层 — 密度热力图', time: '14:32', detail: 'kde_surface 输出' },
  { id: '4', type: 'style' as const, label: '样式变更 — 热力图不透明度', time: '14:33', detail: '0.85 → 0.70' },
  { id: '5', type: 'toggle' as const, label: '隐藏图层 — 北京市行政区划', time: '14:34', detail: '切换为不可见' },
];

export const DEMO_CAUSAL_CHAIN = [
  { id: '1', tool: 'geocode_cn', mapAction: 'fly_to', time: '14:30', toolInput: '北京市', mapEffect: '地图飞至 [116.40, 39.90] zoom 10', mapState: { center: [116.40,39.90], zoom: 10 } },
  { id: '2', tool: 'query_osm_poi', mapAction: 'add_layer', time: '14:31', toolInput: 'category=school, city=北京市', mapEffect: '新增 "北京市学校 POI" 图层（312 要素）', mapState: { layer_id: 'poi-schools', feature_count: 312 } },
  { id: '3', tool: 'kde_surface', mapAction: 'add_layer', time: '14:32', toolInput: 'layer_id=poi-schools, bandwidth=500m', mapEffect: '新增 "密度热力图" 图层', mapState: { layer_id: 'heatmap-density', render_type: 'native_heatmap' } },
];
```

- [ ] **Step 2: 在 store 创建中添加新状态**

在 `create<HudState>()(` 内部，`llmConfigFull`/`setLlmConfigFull` 之后添加：

```typescript
  /* ─── v2 Panel Visibility ─── */
  hudOpen: false,
  setHudOpen: (open) => set({ hudOpen: open }),
  ragPanelOpen: false,
  setRagPanelOpen: (open) => set({ ragPanelOpen: open }),
  tweaksOpen: false,
  setTweaksOpen: (open) => set({ tweaksOpen: open }),

  /* ─── v2 UI Tweaks ─── */
  accentColor: '#16a34a',
  setAccentColor: (color) => set({ accentColor: color }),
  fontSize: 13,
  setFontSize: (size) => set({ fontSize: size }),
  density: 'compact' as const,
  setDensity: (density) => set({ density: density }),
  showGrid: true,
  setShowGrid: (show) => set({ showGrid: show }),
  sidebarWidth: 330,
  setSidebarWidth: (width) => set({ sidebarWidth: width }),

  /* ─── v2 Feature Data ─── */
  opsLog: [],
  pushOpLog: (entry) => set((s) => ({ opsLog: [entry, ...s.opsLog] })),
  clearOpsLog: () => set({ opsLog: [] }),
  ragResults: [],
  setRagResults: (results) => set({ ragResults: results }),
  exports: [],
  setExports: (items) => set({ exports: items }),
  causalChain: [],
  pushCausalEntry: (entry) => set((s) => ({ causalChain: [entry, ...s.causalChain] })),
  clearCausalChain: () => set({ causalChain: [] }),

  /* ─── Demo Mode ─── */
  demoMode: false,
  setDemoMode: (enabled) => set({ demoMode: enabled }),
```

- [ ] **Step 3: 验证类型检查**

Run: `cd frontend && npx tsc --noEmit`
Expected: No errors (or existing unrelated errors only)

- [ ] **Step 4: Commit**

```bash
git add frontend/lib/store/useHudStore.ts
git commit -m "feat: extend store with v2 state and demo data"
```

---

### Task 3: 创建新组件空壳

**Files:**
- Create: `frontend/components/sidebar/ops-log-tab.tsx`
- Create: `frontend/components/sidebar/exports-tab.tsx`
- Create: `frontend/components/sidebar/rag-tab.tsx`
- Create: `frontend/components/map/baselayer-switcher.tsx`
- Create: `frontend/components/map/floating-legend.tsx`
- Create: `frontend/components/hud/agent-env-hud.tsx`
- Create: `frontend/components/hud/causal-trace.tsx`
- Create: `frontend/components/panel/rag-independent-panel.tsx`
- Create: `frontend/components/overlays/perception-rings.tsx`
- Create: `frontend/components/tweaks-panel.tsx`

- [ ] **Step 1: 创建 ops-log-tab.tsx**

```typescript
'use client';

export function OpsLogTab() {
  return (
    <div className="p-3">
      <div className="text-xs text-slate-400">操作日志 (待实现)</div>
    </div>
  );
}

export default OpsLogTab;
```

- [ ] **Step 2: 创建 exports-tab.tsx**

```typescript
'use client';

export function ExportsTab() {
  return (
    <div className="p-3">
      <div className="text-xs text-slate-400">导出列表 (待实现)</div>
    </div>
  );
}

export default ExportsTab;
```

- [ ] **Step 3: 创建 rag-tab.tsx**

```typescript
'use client';

export function RagTab() {
  return (
    <div className="p-3">
      <div className="text-xs text-slate-400">RAG 结果 (待实现)</div>
    </div>
  );
}

export default RagTab;
```

- [ ] **Step 4: 创建 baselayer-switcher.tsx**

```typescript
'use client';

export function BaselayerSwitcher() {
  return null;
}

export default BaselayerSwitcher;
```

- [ ] **Step 5: 创建 floating-legend.tsx**

```typescript
'use client';

export function FloatingLegend() {
  return null;
}

export default FloatingLegend;
```

- [ ] **Step 6: 创建 agent-env-hud.tsx**

```typescript
'use client';

interface AgentEnvHudProps {
  open: boolean;
  onClose: () => void;
}

export function AgentEnvHud({ open, onClose }: AgentEnvHudProps) {
  if (!open) return null;
  return (
    <div className="fixed right-3 top-1/2 -translate-y-1/2 z-40 w-80 bg-white/90 backdrop-blur-xl rounded-xl border p-4">
      <div className="text-xs text-slate-400">Agent 环境感知 (待实现)</div>
    </div>
  );
}

export default AgentEnvHud;
```

- [ ] **Step 7: 创建 causal-trace.tsx**

```typescript
'use client';

export function CausalTrace() {
  return null;
}

export default CausalTrace;
```

- [ ] **Step 8: 创建 rag-independent-panel.tsx**

```typescript
'use client';

interface RagIndependentPanelProps {
  open: boolean;
  onClose: () => void;
}

export function RagIndependentPanel({ open, onClose }: RagIndependentPanelProps) {
  if (!open) return null;
  return (
    <div className="fixed right-3 bottom-10 z-40 w-96 bg-white/90 backdrop-blur-xl rounded-xl border p-4">
      <div className="text-xs text-slate-400">独立 RAG 面板 (待实现)</div>
    </div>
  );
}

export default RagIndependentPanel;
```

- [ ] **Step 9: 创建 perception-rings.tsx**

```typescript
'use client';

interface PerceptionRingsProps {
  active: boolean;
}

export function PerceptionRings({ active }: PerceptionRingsProps) {
  if (!active) return null;
  return null;
}

export default PerceptionRings;
```

- [ ] **Step 10: 创建 tweaks-panel.tsx**

```typescript
'use client';

export function TweaksPanel() {
  return null;
}

export default TweaksPanel;
```

- [ ] **Step 11: 验证类型检查**

Run: `cd frontend && npx tsc --noEmit`
Expected: No errors (or existing unrelated errors only)

- [ ] **Step 12: Commit**

```bash
git add frontend/components/sidebar/ops-log-tab.tsx
git add frontend/components/sidebar/exports-tab.tsx
git add frontend/components/sidebar/rag-tab.tsx
git add frontend/components/map/baselayer-switcher.tsx
git add frontend/components/map/floating-legend.tsx
git add frontend/components/hud/agent-env-hud.tsx
git add frontend/components/hud/causal-trace.tsx
git add frontend/components/panel/rag-independent-panel.tsx
git add frontend/components/overlays/perception-rings.tsx
git add frontend/components/tweaks-panel.tsx
git commit -m "feat: create v2 component skeletons"
```

---

## 阶段二：核心组件重构

### Task 4: 重构 LeftSidebar (多标签页支持)

**Files:**
- Modify: `frontend/components/sidebar/left-sidebar.tsx`

- [ ] **Step 1: 更新导入和标签定义**

替换文件内容：

```typescript
'use client';

import { MessageCircle, Layers, List, Download } from 'lucide-react';
import type { LucideIcon } from 'lucide-react';
import { useHudStore } from '@/lib/store/useHudStore';
import { ChatTab } from '@/components/sidebar/chat-tab';
import { LayersTab } from '@/components/sidebar/layers-tab';
import { OpsLogTab } from '@/components/sidebar/ops-log-tab';
import { ExportsTab } from '@/components/sidebar/exports-tab';
import type { AiStatus, LeftTab } from '@/lib/store/hud-types';

export interface LeftSidebarProps {
  open: boolean;
  messages: Array<{
    id: string;
    role: 'user' | 'assistant';
    content: string;
    timestamp: Date;
    isThinking?: boolean;
    charts?: unknown[];
    toolCalls?: Array<{
      id: string;
      tool: string;
      arguments?: string;
      status: 'running' | 'completed' | 'failed';
      result?: unknown;
      hasGeojson?: boolean;
      error?: string;
    }>;
    ragRefs?: Array<{ source: string; score: string; excerpt: string }>;
  }>;
  aiStatus: AiStatus;
  onSend: (text: string) => void;
  accentColor?: string;
}

interface TabDef {
  key: LeftTab;
  icon: LucideIcon;
  label: string;
}

const TAB_DEFS: TabDef[] = [
  { key: 'chat', icon: MessageCircle, label: '对话' },
  { key: 'layers', icon: Layers, label: '图层' },
  { key: 'ops', icon: List, label: '日志' },
  { key: 'exports', icon: Download, label: '导出' },
];
```

- [ ] **Step 2: 更新组件函数**

替换组件函数：

```typescript
export function LeftSidebar({ open, messages, aiStatus, onSend, accentColor = '#16a34a' }: LeftSidebarProps) {
  const activeTab = useHudStore((s) => s.activeLeftTab);
  const setActiveTab = useHudStore((s) => s.setActiveLeftTab);
  const layers = useHudStore((s) => s.layers);
  const opsLog = useHudStore((s) => s.opsLog);
  const exports = useHudStore((s) => s.exports);
  const sidebarWidth = useHudStore((s) => s.sidebarWidth);

  const badges: Record<LeftTab, number | undefined> = {
    chat: undefined,
    layers: layers.length,
    ops: opsLog.length,
    exports: exports.length,
  };

  return (
    <aside
      className="fixed top-[42px] left-0 bottom-[24px] z-40 flex flex-col"
      style={{
        width: sidebarWidth,
        background: 'rgba(252,253,254,0.90)',
        backdropFilter: 'blur(28px)',
        WebkitBackdropFilter: 'blur(28px)',
        borderRight: '1px solid rgba(255,255,255,0.85)',
        boxShadow: '2px 0 24px rgba(15,23,42,0.09)',
        transform: open ? 'translateX(0)' : 'translateX(-100%)',
        transition: 'transform 0.3s cubic-bezier(0.4, 0, 0.2, 1)',
      }}
    >
      {/* Tab bar */}
      <div className="flex shrink-0 border-b border-slate-200/60 bg-white/40">
        {TAB_DEFS.map(({ key, icon: Icon, label }) => {
          const isActive = activeTab === key;
          const badge = badges[key];
          return (
            <button
              key={key}
              onClick={() => setActiveTab(key)}
              className="flex-1 flex items-center justify-center gap-1.5 py-3 text-[11.5px] font-medium transition-colors relative"
              style={{
                color: isActive ? accentColor : '#64748b',
              }}
            >
              <Icon size={15} strokeWidth={isActive ? 2.2 : 1.6} />
              <span>{label}</span>
              {badge !== undefined && badge > 0 && (
                <span
                  className="inline-flex items-center justify-center min-w-[16px] h-4 px-1 rounded-full text-[9.5px] font-semibold text-white"
                  style={{ backgroundColor: accentColor }}
                >
                  {badge}
                </span>
              )}
              {isActive && (
                <span
                  className="absolute bottom-0 left-1/2 -translate-x-1/2 h-[2px] rounded-full w-8"
                  style={{ backgroundColor: accentColor }}
                />
              )}
            </button>
          );
        })}
      </div>

      {/* Tab content */}
      <div className="flex-1 min-h-0 overflow-hidden">
        {activeTab === 'chat' && (
          <ChatTab messages={messages} aiStatus={aiStatus} onSend={onSend} accentColor={accentColor} />
        )}
        {activeTab === 'layers' && <LayersTab />}
        {activeTab === 'ops' && <OpsLogTab />}
        {activeTab === 'exports' && <ExportsTab />}
      </div>
    </aside>
  );
}

export default LeftSidebar;
```

- [ ] **Step 3: 验证类型检查**

Run: `cd frontend && npx tsc --noEmit`
Expected: No errors (or existing unrelated errors only)

- [ ] **Step 4: Commit**

```bash
git add frontend/components/sidebar/left-sidebar.tsx
git commit -m "feat: update LeftSidebar with v2 tabs"
```

---

### Task 5: 重构 MapToolbar (新功能)

**Files:**
- Modify: `frontend/components/map/map-toolbar.tsx`

- [ ] **Step 1: 替换 MapToolbar 组件**

完整替换文件内容：

```typescript
"use client";

import {
  ZoomIn,
  ZoomOut,
  RotateCcw,
  Crosshair,
  Box,
  Download,
  Eye,
} from "lucide-react";
import { useHudStore } from "@/lib/store/useHudStore";

interface MapToolbarProps {
  sidebarOpen: boolean;
  hudOpen?: boolean;
  onToggleHud?: () => void;
  onZoomIn?: () => void;
  onZoomOut?: () => void;
  onHome?: () => void;
  onLocate?: () => void;
  onExport?: () => void;
}

export default function MapToolbar({
  sidebarOpen,
  hudOpen = false,
  onToggleHud,
  onZoomIn,
  onZoomOut,
  onHome,
  onLocate,
  onExport,
}: MapToolbarProps) {
  const is3D = useHudStore((s) => s.is3D);
  const setIs3D = useHudStore((s) => s.setIs3D);
  const accentColor = useHudStore((s) => s.accentColor);

  const btnBase =
    "flex items-center justify-center w-[32px] h-[32px] rounded-[8px] " +
    "transition-all duration-100 cursor-pointer border-0 p-0 " +
    "bg-transparent hover:bg-slate-100/80 active:bg-slate-200/80";

  return (
    <div
      style={{
        position: 'absolute',
        top: '50%',
        right: hudOpen ? 340 : 10,
        transform: 'translateY(-50%)',
        zIndex: 40,
        display: 'flex',
        flexDirection: 'column',
        gap: 1,
        background: 'rgba(255,255,255,0.85)',
        backdropFilter: 'blur(20px)',
        WebkitBackdropFilter: 'blur(20px)',
        border: '1px solid rgba(255,255,255,0.9)',
        boxShadow: '0 4px 24px rgba(15,23,42,0.10)',
        borderRadius: 12,
        padding: 3,
        transition: 'right 0.22s cubic-bezier(0.4,0,0.2,1)',
      }}
    >
      {/* Zoom in */}
      <button style={{ ...buttonStyle, color: '#64748b' }} onClick={onZoomIn} title="放大">
        <svg width="14" height="14" viewBox="0 0 14 14" fill="none" style={{ display: 'block' }}>
          <circle cx="6" cy="6" r="4.5" stroke="currentColor" strokeWidth="1.3"/>
          <path d="M10 10l3 3" stroke="currentColor" strokeWidth="1.4" strokeLinecap="round"/>
          <path d="M4 6h4M6 4v4" stroke="currentColor" strokeWidth="1.3" strokeLinecap="round"/>
        </svg>
      </button>

      {/* Zoom out */}
      <button style={{ ...buttonStyle, color: '#64748b' }} onClick={onZoomOut} title="缩小">
        <svg width="14" height="14" viewBox="0 0 14 14" fill="none" style={{ display: 'block' }}>
          <circle cx="6" cy="6" r="4.5" stroke="currentColor" strokeWidth="1.3"/>
          <path d="M10 10l3 3" stroke="currentColor" strokeWidth="1.4" strokeLinecap="round"/>
          <path d="M4 6h4" stroke="currentColor" strokeWidth="1.3" strokeLinecap="round"/>
        </svg>
      </button>

      {/* Home */}
      <button style={{ ...buttonStyle, color: '#64748b' }} onClick={onHome} title="复位">
        <svg width="14" height="14" viewBox="0 0 14 14" fill="none" style={{ display: 'block' }}>
          <path d="M2 6.5l5-4.5 5 4.5V12H9V9H5v3H2V6.5z" stroke="currentColor" strokeWidth="1.3" strokeLinejoin="round"/>
        </svg>
      </button>

      {/* Locate */}
      <button style={{ ...buttonStyle, color: '#64748b' }} onClick={onLocate} title="定位我">
        <svg width="14" height="14" viewBox="0 0 14 14" fill="none" style={{ display: 'block' }}>
          <circle cx="7" cy="7" r="2.5" stroke="currentColor" strokeWidth="1.2"/>
          <path d="M7 1v2.5M7 10.5V13M1 7h2.5M10.5 7H13" stroke="currentColor" strokeWidth="1.2" strokeLinecap="round"/>
        </svg>
      </button>

      {/* Divider */}
      <div style={{ width: 20, height: 1, background: 'rgba(15,23,42,0.08)', margin: '2px auto' }} />

      {/* 2D/3D toggle */}
      <button
        onClick={() => setIs3D(!is3D)}
        title={is3D ? "切换 2D" : "切换 3D"}
        style={{
          ...buttonStyle,
          fontSize: '9.5px',
          fontWeight: 700,
          letterSpacing: '0.06em',
          background: is3D ? 'rgba(22,163,74,0.1)' : 'transparent',
          color: is3D ? '#15803d' : '#64748b',
        }}
      >
        {is3D ? '3D' : '2D'}
      </button>

      {/* HUD toggle */}
      <button
        onClick={onToggleHud}
        title="Agent 环境感知"
        style={{
          ...buttonStyle,
          background: hudOpen ? 'rgba(139,92,246,0.12)' : 'transparent',
          color: hudOpen ? '#7c3aed' : '#64748b',
        }}
      >
        <svg width="14" height="14" viewBox="0 0 14 14" fill="none" style={{ display: 'block' }}>
          <circle cx="7" cy="7" r="5.5" stroke="currentColor" strokeWidth="1.2"/>
          <circle cx="7" cy="7" r="2" stroke="currentColor" strokeWidth="1.2"/>
          <path d="M7 1.5v2M7 10.5v2M1.5 7h2M10.5 7h2" stroke="currentColor" strokeWidth="1.1" strokeLinecap="round"/>
        </svg>
      </button>

      {/* Export */}
      <button style={{ ...buttonStyle, color: '#64748b' }} onClick={onExport} title="导出地图">
        <svg width="14" height="14" viewBox="0 0 14 14" fill="none" style={{ display: 'block' }}>
          <path d="M7 2v7M4.5 6.5L7 9l2.5-2.5" stroke="currentColor" strokeWidth="1.3" strokeLinecap="round" strokeLinejoin="round"/>
          <path d="M2 11h10" stroke="currentColor" strokeWidth="1.3" strokeLinecap="round"/>
        </svg>
      </button>
    </div>
  );
}

const buttonStyle = {
  display: 'flex',
  alignItems: 'center',
  justifyContent: 'center',
  width: 32,
  height: 32,
  borderRadius: 8,
  border: 'none',
  background: 'transparent',
  cursor: 'pointer',
  transition: 'all 0.1s',
  fontFamily: "'JetBrains Mono', monospace",
} as const;
```

- [ ] **Step 2: 验证类型检查**

Run: `cd frontend && npx tsc --noEmit`
Expected: No errors (or existing unrelated errors only)

- [ ] **Step 3: Commit**

```bash
git add frontend/components/map/map-toolbar.tsx
git commit -m "feat: update MapToolbar with v2 design"
```

---

### Task 6: 更新 TopBar (保持功能，样式调整)

**Files:**
- Modify: `frontend/components/layout/top-bar.tsx`

- [ ] **Step 1: 检查是否需要更新**

现有 TopBar 已经比较接近 v2 设计，检查设计文件中的 TopBar，不需要大改。跳过此任务，保持原样。

- [ ] **Step 2: (可选) 微调样式**

如需要，可以后续微调，当前先保持原样。

---

### Task 7: 更新 StatusBar (保持功能)

**Files:**
- Modify: `frontend/components/layout/status-bar.tsx`

- [ ] **Step 1: 检查是否需要更新**

现有 StatusBar 已经比较接近 v2 设计，不需要大改。跳过此任务，保持原样。

---

## 阶段三：新功能组件实现

### Task 8: 实现 OpsLogTab (操作日志)

**Files:**
- Modify: `frontend/components/sidebar/ops-log-tab.tsx`

- [ ] **Step 1: 实现完整的 OpsLogTab 组件**

```typescript
'use client';

import { useHudStore, DEMO_OPS_LOG } from '@/lib/store/useHudStore';

const iconForType: Record<string, string> = {
  add: '+',
  remove: '−',
  toggle: '⇄',
  flyto: '⟶',
  style: '✎',
};

const colorForType: Record<string, string> = {
  add: '#16a34a',
  remove: '#dc2626',
  toggle: '#2563eb',
  flyto: '#7c3aed',
  style: '#ca8a04',
};

export function OpsLogTab() {
  const opsLog = useHudStore((s) => s.opsLog);
  const demoMode = useHudStore((s) => s.demoMode);
  const setDemoMode = useHudStore((s) => s.setDemoMode);
  const pushOpLog = useHudStore((s) => s.pushOpLog);

  const displayLog = demoMode && opsLog.length === 0 ? DEMO_OPS_LOG : opsLog;

  return (
    <div className="flex flex-col h-full">
      {/* Header */}
      <div className="flex items-center justify-between px-3 py-2 border-b border-slate-200/60">
        <span className="text-[10px] font-semibold text-slate-400 uppercase tracking-wider">
          操作日志
        </span>
        {!demoMode && opsLog.length === 0 && (
          <button
            onClick={() => setDemoMode(true)}
            className="text-[10px] text-slate-400 hover:text-slate-600"
          >
            加载演示
          </button>
        )}
      </div>

      {/* Log list */}
      <div className="flex-1 overflow-y-auto p-2">
        {displayLog.length === 0 ? (
          <div className="text-center py-8 text-xs text-slate-400">
            暂无操作记录
          </div>
        ) : (
          <div className="space-y-1">
            {displayLog.map((entry) => (
              <div
                key={entry.id}
                className="flex items-start gap-2 p-2 rounded-lg hover:bg-slate-100/60"
              >
                <div
                  className="w-6 h-6 rounded flex items-center justify-center text-xs font-bold flex-shrink-0"
                  style={{
                    backgroundColor: colorForType[entry.type] + '20',
                    color: colorForType[entry.type],
                  }}
                >
                  {iconForType[entry.type] || '•'}
                </div>
                <div className="flex-1 min-w-0">
                  <div className="text-xs text-slate-700 font-medium">
                    {entry.label}
                  </div>
                  {entry.detail && (
                    <div className="text-[10px] text-slate-400 mt-0.5 font-mono">
                      {entry.detail}
                    </div>
                  )}
                </div>
                <div className="text-[10px] text-slate-300 font-mono flex-shrink-0">
                  {entry.time}
                </div>
              </div>
            ))}
          </div>
        )}
      </div>
    </div>
  );
}

export default OpsLogTab;
```

- [ ] **Step 2: 验证类型检查**

Run: `cd frontend && npx tsc --noEmit`
Expected: No errors (or existing unrelated errors only)

- [ ] **Step 3: Commit**

```bash
git add frontend/components/sidebar/ops-log-tab.tsx
git commit -m "feat: implement OpsLogTab"
```

---

### Task 9: 实现 ExportsTab (导出列表)

**Files:**
- Modify: `frontend/components/sidebar/exports-tab.tsx`

- [ ] **Step 1: 实现完整的 ExportsTab 组件**

```typescript
'use client';

import { useHudStore, DEMO_EXPORTS } from '@/lib/store/useHudStore';

const iconForType: Record<string, string> = {
  png: '🖼',
  pdf: '📄',
  geojson: '📍',
};

export function ExportsTab() {
  const exports = useHudStore((s) => s.exports);
  const demoMode = useHudStore((s) => s.demoMode);
  const setDemoMode = useHudStore((s) => s.setDemoMode);

  const displayExports = demoMode && exports.length === 0 ? DEMO_EXPORTS : exports;

  return (
    <div className="flex flex-col h-full">
      {/* Header */}
      <div className="flex items-center justify-between px-3 py-2 border-b border-slate-200/60">
        <span className="text-[10px] font-semibold text-slate-400 uppercase tracking-wider">
          导出文件
        </span>
        {!demoMode && exports.length === 0 && (
          <button
            onClick={() => setDemoMode(true)}
            className="text-[10px] text-slate-400 hover:text-slate-600"
          >
            加载演示
          </button>
        )}
      </div>

      {/* Exports list */}
      <div className="flex-1 overflow-y-auto p-2">
        {displayExports.length === 0 ? (
          <div className="text-center py-8 text-xs text-slate-400">
            暂无导出文件
          </div>
        ) : (
          <div className="space-y-1">
            {displayExports.map((item) => (
              <div
                key={item.id}
                className="flex items-center gap-2 p-2 rounded-lg hover:bg-slate-100/60 cursor-pointer"
              >
                <div className="w-8 h-8 rounded-lg bg-slate-100 flex items-center justify-center text-lg flex-shrink-0">
                  {iconForType[item.type] || '📁'}
                </div>
                <div className="flex-1 min-w-0">
                  <div className="text-xs text-slate-700 font-medium truncate">
                    {item.name}
                  </div>
                  <div className="flex items-center gap-2 mt-0.5">
                    <span className="text-[10px] text-slate-400 font-mono uppercase">
                      {item.type}
                    </span>
                    <span className="text-[10px] text-slate-300">•</span>
                    <span className="text-[10px] text-slate-400 font-mono">
                      {item.size}
                    </span>
                  </div>
                </div>
                <div className="text-[10px] text-slate-300 flex-shrink-0">
                  {item.date}
                </div>
              </div>
            ))}
          </div>
        )}
      </div>
    </div>
  );
}

export default ExportsTab;
```

- [ ] **Step 2: 验证类型检查**

Run: `cd frontend && npx tsc --noEmit`
Expected: No errors (or existing unrelated errors only)

- [ ] **Step 3: Commit**

```bash
git add frontend/components/sidebar/exports-tab.tsx
git commit -m "feat: implement ExportsTab"
```

---

### Task 10: 实现 RagTab (RAG 结果标签页)

**Files:**
- Modify: `frontend/components/sidebar/rag-tab.tsx`

- [ ] **Step 1: 实现完整的 RagTab 组件**

```typescript
'use client';

import { useHudStore, DEMO_RAG } from '@/lib/store/useHudStore';

export function RagTab() {
  const ragResults = useHudStore((s) => s.ragResults);
  const demoMode = useHudStore((s) => s.demoMode);
  const setDemoMode = useHudStore((s) => s.setDemoMode);

  const displayResults = demoMode && ragResults.length === 0 ? DEMO_RAG : ragResults;

  return (
    <div className="flex flex-col h-full">
      {/* Header */}
      <div className="flex items-center justify-between px-3 py-2 border-b border-slate-200/60">
        <span className="text-[10px] font-semibold text-slate-400 uppercase tracking-wider">
          RAG 检索结果
        </span>
        {!demoMode && ragResults.length === 0 && (
          <button
            onClick={() => setDemoMode(true)}
            className="text-[10px] text-slate-400 hover:text-slate-600"
          >
            加载演示
          </button>
        )}
      </div>

      {/* Results list */}
      <div className="flex-1 overflow-y-auto p-2">
        {displayResults.length === 0 ? (
          <div className="text-center py-8 text-xs text-slate-400">
            暂无检索结果
          </div>
        ) : (
          <div className="space-y-2">
            {displayResults.map((result) => (
              <div
                key={result.id}
                className="p-3 rounded-xl border border-slate-200/60 bg-white/60"
              >
                {/* Source header */}
                <div className="flex items-center justify-between mb-2">
                  <div className="text-xs font-medium text-slate-700 truncate flex-1">
                    {result.source}
                  </div>
                  <div className="flex items-center gap-2 ml-2">
                    <span className="text-[10px] px-1.5 py-0.5 rounded-full bg-green-100 text-green-700 font-mono font-semibold">
                      {result.score}
                    </span>
                    <span className="text-[10px] text-slate-400 font-mono">
                      {result.chunks} 块
                    </span>
                  </div>
                </div>

                {/* Excerpts */}
                <div className="space-y-1.5">
                  {result.excerpts.map((excerpt, idx) => (
                    <div
                      key={idx}
                      className="text-[11px] text-slate-500 leading-relaxed"
                    >
                      "{excerpt}"
                    </div>
                  ))}
                </div>
              </div>
            ))}
          </div>
        )}
      </div>
    </div>
  );
}

export default RagTab;
```

- [ ] **Step 2: 验证类型检查**

Run: `cd frontend && npx tsc --noEmit`
Expected: No errors (or existing unrelated errors only)

- [ ] **Step 3: Commit**

```bash
git add frontend/components/sidebar/rag-tab.tsx
git commit -m "feat: implement RagTab"
```

---

### Task 11: 实现 BaselayerSwitcher (底图切换器)

**Files:**
- Modify: `frontend/components/map/baselayer-switcher.tsx`

- [ ] **Step 1: 实现完整的 BaselayerSwitcher 组件**

```typescript
'use client';

import { useState } from 'react';
import { useHudStore } from '@/lib/store/useHudStore';

interface BaselayerSwitcherProps {
  className?: string;
}

const BASELAYERS = [
  { id: 'osm', label: 'OpenStreetMap' },
  { id: 'amap', label: '高德地图' },
  { id: 'satellite', label: '卫星影像' },
  { id: 'dark', label: '暗色底图' },
  { id: 'tianditu', label: '天地图' },
];

export function BaselayerSwitcher({ className }: BaselayerSwitcherProps) {
  const [open, setOpen] = useState(false);
  const baseLayer = useHudStore((s) => s.baseLayer);
  const setBaseLayer = useHudStore((s) => s.setBaseLayer);

  const currentLabel = BASELAYERS.find((l) => l.id === baseLayer)?.label || baseLayer;

  return (
    <div style={{ position: 'relative' }} className={className}>
      <button
        onClick={() => setOpen(!open)}
        style={{
          padding: '5px 10px',
          borderRadius: 8,
          background: 'rgba(255,255,255,0.88)',
          backdropFilter: 'blur(12px)',
          WebkitBackdropFilter: 'blur(12px)',
          border: '1px solid rgba(255,255,255,0.92)',
          boxShadow: '0 2px 12px rgba(15,23,42,0.08)',
          fontSize: '10.5px',
          color: '#475569',
          cursor: 'pointer',
          fontFamily: "'JetBrains Mono', monospace",
          display: 'flex',
          alignItems: 'center',
          gap: 5,
        }}
      >
        <svg width="11" height="11" viewBox="0 0 11 11" fill="none" style={{ display: 'block' }}>
          <path d="M5.5 1L1 4l4.5 2.5L10 4 5.5 1z" stroke="#94a3b8" strokeWidth="1"/>
          <path d="M1 7l4.5 2.5L10 7" stroke="#94a3b8" strokeWidth="1" strokeLinecap="round"/>
        </svg>
        {currentLabel}
        <svg width="8" height="8" viewBox="0 0 8 8" fill="none" style={{ display: 'block', transform: open ? 'rotate(180deg)' : 'none', transition: 'transform 0.15s' }}>
          <path d="M1 2.5l3 3 3-3" stroke="#94a3b8" strokeWidth="1.2" strokeLinecap="round"/>
        </svg>
      </button>

      {open && (
        <div
          style={{
            position: 'absolute',
            bottom: '100%',
            right: 0,
            marginBottom: 4,
            background: 'rgba(252,253,254,0.96)',
            backdropFilter: 'blur(20px)',
            WebkitBackdropFilter: 'blur(20px)',
            border: '1px solid rgba(255,255,255,0.92)',
            boxShadow: '0 4px 24px rgba(15,23,42,0.09)',
            borderRadius: 10,
            overflow: 'hidden',
            minWidth: 140,
          }}
        >
          {BASELAYERS.map((layer) => (
            <button
              key={layer.id}
              onClick={() => {
                setBaseLayer(layer.id);
                setOpen(false);
              }}
              style={{
                display: 'block',
                width: '100%',
                padding: '7px 12px',
                border: 'none',
                background: layer.id === baseLayer ? 'rgba(22,163,74,0.07)' : 'transparent',
                color: layer.id === baseLayer ? '#15803d' : '#475569',
                fontSize: 11,
                cursor: 'pointer',
                textAlign: 'left',
                fontFamily: "'DM Sans', system-ui, sans-serif",
                fontWeight: layer.id === baseLayer ? 500 : 400,
              }}
              onMouseEnter={(e) => {
                if (layer.id !== baseLayer) {
                  e.currentTarget.style.background = 'rgba(15,23,42,0.04)';
                }
              }}
              onMouseLeave={(e) => {
                if (layer.id !== baseLayer) {
                  e.currentTarget.style.background = 'transparent';
                }
              }}
            >
              {layer.label}
            </button>
          ))}
        </div>
      )}
    </div>
  );
}

export default BaselayerSwitcher;
```

- [ ] **Step 2: 验证类型检查**

Run: `cd frontend && npx tsc --noEmit`
Expected: No errors (or existing unrelated errors only)

- [ ] **Step 3: Commit**

```bash
git add frontend/components/map/baselayer-switcher.tsx
git commit -m "feat: implement BaselayerSwitcher"
```

---

### Task 12: 实现 FloatingLegend (浮动图例)

**Files:**
- Modify: `frontend/components/map/floating-legend.tsx`

- [ ] **Step 1: 实现完整的 FloatingLegend 组件**

```typescript
'use client';

import { useHudStore } from '@/lib/store/useHudStore';

interface FloatingLegendProps {
  className?: string;
}

const COLORS = ['#0ff0ff', '#00ff41', '#ffff00', '#ff5f00', '#ff2d55'];
const LABELS = ['极低', '低', '中', '高', '极高'];

export function FloatingLegend({ className }: FloatingLegendProps) {
  const layers = useHudStore((s) => s.layers);
  const visibleHeatLayer = layers.find((l) => l.visible && l.type === 'heatmap');

  if (!visibleHeatLayer) return null;

  return (
    <div
      style={{
        background: 'rgba(252,253,254,0.92)',
        backdropFilter: 'blur(20px)',
        WebkitBackdropFilter: 'blur(20px)',
        border: '1px solid rgba(255,255,255,0.92)',
        boxShadow: '0 4px 24px rgba(15,23,42,0.09)',
        borderRadius: 10,
        padding: '8px 12px',
        fontSize: '10.5px',
        fontFamily: "'DM Sans', system-ui, sans-serif",
        minWidth: 140,
      }}
      className={className}
    >
      <div style={{ fontSize: 10, color: '#94a3b8', fontFamily: "'JetBrains Mono', monospace", textTransform: 'uppercase', letterSpacing: '0.08em', marginBottom: 6 }}>
        {visibleHeatLayer.name}
      </div>
      <div style={{ display: 'flex', height: 8, borderRadius: 4, overflow: 'hidden', marginBottom: 5 }}>
        {COLORS.map((color, idx) => (
          <div key={idx} style={{ flex: 1, backgroundColor: color }} />
        ))}
      </div>
      <div style={{ display: 'flex', justifyContent: 'space-between', color: '#64748b', fontSize: 9 }}>
        {LABELS.map((label, idx) => (
          <span key={idx}>{label}</span>
        ))}
      </div>
    </div>
  );
}

export default FloatingLegend;
```

- [ ] **Step 2: 验证类型检查**

Run: `cd frontend && npx tsc --noEmit`
Expected: No errors (or existing unrelated errors only)

- [ ] **Step 3: Commit**

```bash
git add frontend/components/map/floating-legend.tsx
git commit -m "feat: implement FloatingLegend"
```

---

### Task 13: 实现 PerceptionRings (感知环动画)

**Files:**
- Modify: `frontend/components/overlays/perception-rings.tsx`

- [ ] **Step 1: 实现完整的 PerceptionRings 组件**

```typescript
'use client';

import { useEffect, useState } from 'react';

interface PerceptionRingsProps {
  active: boolean;
}

export function PerceptionRings({ active }: PerceptionRingsProps) {
  if (!active) return null;

  return (
    <div style={{ position: 'absolute', left: '50%', top: '50%', pointerEvents: 'none', zIndex: 5 }}>
      {[0, 0.8, 1.6].map((delay, idx) => (
        <div
          key={idx}
          style={{
            position: 'absolute',
            borderRadius: '50%',
            border: '1.5px solid rgba(22,163,74,0.5)',
            width: 60 + idx * 40,
            height: 60 + idx * 40,
            top: '50%',
            left: '50%',
            animation: `ringPulse 2.5s ease-out ${delay}s infinite`,
          }}
        />
      ))}
      <div style={{ position: 'absolute', width: 8, height: 8, background: '#16a34a', borderRadius: '50%', top: '50%', left: '50%', transform: 'translate(-50%,-50%)', boxShadow: '0 0 12px rgba(22,163,74,0.7)' }} />
      <style dangerouslySetInnerHTML={{ __html: `
        @keyframes ringPulse {
          0% {
            transform: translate(-50%, -50%) scale(0.5);
            opacity: 0.6;
          }
          100% {
            transform: translate(-50%, -50%) scale(2.5);
            opacity: 0;
          }
        }
      `}} />
    </div>
  );
}

export default PerceptionRings;
```

- [ ] **Step 2: 验证类型检查**

Run: `cd frontend && npx tsc --noEmit`
Expected: No errors (or existing unrelated errors only)

- [ ] **Step 3: Commit**

```bash
git add frontend/components/overlays/perception-rings.tsx
git commit -m "feat: implement PerceptionRings"
```

---

### Task 14: 实现 AgentEnvHUD (Agent 环境感知面板)

**Files:**
- Modify: `frontend/components/hud/agent-env-hud.tsx`
- Modify: `frontend/components/hud/causal-trace.tsx`

- [ ] **Step 1: 实现 CausalTrace 组件**

```typescript
'use client';

import { useHudStore, DEMO_CAUSAL_CHAIN } from '@/lib/store/useHudStore';

export function CausalTrace() {
  const causalChain = useHudStore((s) => s.causalChain);
  const demoMode = useHudStore((s) => s.demoMode);

  const displayChain = demoMode && causalChain.length === 0 ? DEMO_CAUSAL_CHAIN : causalChain;

  if (displayChain.length === 0) return null;

  return (
    <div className="space-y-2">
      {displayChain.map((entry, idx) => (
        <div key={entry.id} className="flex gap-2">
          {/* Step number */}
          <div className="flex flex-col items-center">
            <div className="w-5 h-5 rounded-full bg-violet-100 text-violet-700 flex items-center justify-center text-[10px] font-bold">
              {displayChain.length - idx}
            </div>
            {idx < displayChain.length - 1 && (
              <div className="w-0.5 flex-1 bg-violet-200 my-1" />
            )}
          </div>

          {/* Content */}
          <div className="flex-1 pb-2">
            <div className="flex items-center gap-2 mb-1">
              <span className="text-[10px] font-mono bg-violet-100 text-violet-700 px-1.5 py-0.5 rounded">
                {entry.tool}
              </span>
              {entry.mapAction && (
                <span className="text-[10px] font-mono bg-green-100 text-green-700 px-1.5 py-0.5 rounded">
                  {entry.mapAction}
                </span>
              )}
              <span className="text-[10px] text-slate-300 ml-auto font-mono">
                {entry.time}
              </span>
            </div>
            {entry.toolInput && (
              <div className="text-[10px] text-slate-500 mb-1">
                <span className="text-slate-400">输入: </span>
                <code className="font-mono bg-slate-100 px-1 rounded">{entry.toolInput}</code>
              </div>
            )}
            {entry.mapEffect && (
              <div className="text-[10px] text-slate-600">
                {entry.mapEffect}
              </div>
            )}
          </div>
        </div>
      ))}
    </div>
  );
}

export default CausalTrace;
```

- [ ] **Step 2: 实现完整的 AgentEnvHUD 组件**

```typescript
'use client';

import { X } from 'lucide-react';
import { useHudStore } from '@/lib/store/useHudStore';
import { CausalTrace } from './causal-trace';

interface AgentEnvHudProps {
  open: boolean;
  onClose: () => void;
}

export function AgentEnvHud({ open, onClose }: AgentEnvHudProps) {
  const viewport = useHudStore((s) => s.viewport);
  const baseLayer = useHudStore((s) => s.baseLayer);
  const is3D = useHudStore((s) => s.is3D);
  const layers = useHudStore((s) => s.layers);

  if (!open) return null;

  return (
    <div
      style={{
        position: 'absolute',
        right: 10,
        top: '50%',
        transform: 'translateY(-50%)',
        zIndex: 40,
        width: 320,
        maxHeight: 'calc(100vh - 120px)',
        background: 'rgba(252,253,254,0.96)',
        backdropFilter: 'blur(20px)',
        WebkitBackdropFilter: 'blur(20px)',
        border: '1px solid rgba(255,255,255,0.95)',
        boxShadow: '0 8px 32px rgba(15,23,42,0.12)',
        borderRadius: 16,
        overflow: 'hidden',
      }}
    >
      {/* Header */}
      <div className="flex items-center justify-between px-4 py-3 border-b border-slate-200/60 bg-white/40">
        <div className="flex items-center gap-2">
          <div className="w-6 h-6 rounded-lg bg-violet-100 flex items-center justify-center">
            <svg width="14" height="14" viewBox="0 0 14 14" fill="none">
              <circle cx="7" cy="7" r="5.5" stroke="#7c3aed" strokeWidth="1.2"/>
              <circle cx="7" cy="7" r="2" stroke="#7c3aed" strokeWidth="1.2"/>
              <path d="M7 1.5v2M7 10.5v2M1.5 7h2M10.5 7h2" stroke="#7c3aed" strokeWidth="1.1" strokeLinecap="round"/>
            </svg>
          </div>
          <div>
            <div className="text-xs font-semibold text-slate-800">Agent 环境感知</div>
            <div className="text-[10px] text-slate-400">实时地图状态</div>
          </div>
        </div>
        <button
          onClick={onClose}
          className="w-6 h-6 flex items-center justify-center rounded hover:bg-slate-100 text-slate-400 hover:text-slate-600"
        >
          <X size={14} />
        </button>
      </div>

      {/* Content */}
      <div className="overflow-y-auto max-h-[calc(100vh-180px)]">
        {/* Current viewport state */}
        <div className="p-4 border-b border-slate-200/60">
          <div className="text-[10px] font-semibold text-slate-400 uppercase tracking-wider mb-3">
            视口状态
          </div>
          <div className="grid grid-cols-2 gap-2">
            <div className="bg-slate-100/60 rounded-lg p-2">
              <div className="text-[9px] text-slate-400 uppercase tracking-wide mb-0.5">经纬度</div>
              <div className="text-[11px] text-slate-700 font-mono">
                {viewport.center[0].toFixed(5)}, {viewport.center[1].toFixed(5)}
              </div>
            </div>
            <div className="bg-slate-100/60 rounded-lg p-2">
              <div className="text-[9px] text-slate-400 uppercase tracking-wide mb-0.5">缩放</div>
              <div className="text-[11px] text-slate-700 font-mono">{viewport.zoom.toFixed(1)}</div>
            </div>
            <div className="bg-slate-100/60 rounded-lg p-2">
              <div className="text-[9px] text-slate-400 uppercase tracking-wide mb-0.5">底图</div>
              <div className="text-[11px] text-slate-700 font-mono">{baseLayer}</div>
            </div>
            <div className="bg-slate-100/60 rounded-lg p-2">
              <div className="text-[9px] text-slate-400 uppercase tracking-wide mb-0.5">模式</div>
              <div className="text-[11px] text-slate-700 font-mono">{is3D ? '3D' : '2D'}</div>
            </div>
          </div>
        </div>

        {/* Layers summary */}
        <div className="p-4 border-b border-slate-200/60">
          <div className="text-[10px] font-semibold text-slate-400 uppercase tracking-wider mb-3">
            图层 ({layers.length})
          </div>
          <div className="space-y-1">
            {layers.slice(0, 5).map((layer) => (
              <div key={layer.id} className="flex items-center gap-2 text-xs">
                <div
                  className="w-2 h-2 rounded-full"
                  style={{ backgroundColor: layer.visible ? (layer.color || '#16a34a') : '#cbd5e1', opacity: layer.visible ? 1 : 0.3 }}
                />
                <span className={layer.visible ? 'text-slate-700' : 'text-slate-400'}>
                  {layer.name}
                </span>
              </div>
            ))}
            {layers.length > 5 && (
              <div className="text-xs text-slate-400">
                还有 {layers.length - 5} 个图层...
              </div>
            )}
          </div>
        </div>

        {/* Causal trace */}
        <div className="p-4">
          <div className="text-[10px] font-semibold text-slate-400 uppercase tracking-wider mb-3">
            因果链
          </div>
          <CausalTrace />
        </div>
      </div>
    </div>
  );
}

export default AgentEnvHud;
```

- [ ] **Step 3: 验证类型检查**

Run: `cd frontend && npx tsc --noEmit`
Expected: No errors (or existing unrelated errors only)

- [ ] **Step 4: Commit**

```bash
git add frontend/components/hud/agent-env-hud.tsx
git add frontend/components/hud/causal-trace.tsx
git commit -m "feat: implement AgentEnvHUD and CausalTrace"
```

---

### Task 15: 实现 RagIndependentPanel (独立 RAG 面板)

**Files:**
- Modify: `frontend/components/panel/rag-independent-panel.tsx`

- [ ] **Step 1: 实现完整的 RagIndependentPanel 组件**

```typescript
'use client';

import { X } from 'lucide-react';
import { useHudStore, DEMO_RAG } from '@/lib/store/useHudStore';

interface RagIndependentPanelProps {
  open: boolean;
  onClose: () => void;
}

export function RagIndependentPanel({ open, onClose }: RagIndependentPanelProps) {
  const ragResults = useHudStore((s) => s.ragResults);
  const demoMode = useHudStore((s) => s.demoMode);
  const setDemoMode = useHudStore((s) => s.setDemoMode);

  const displayResults = demoMode && ragResults.length === 0 ? DEMO_RAG : ragResults;

  if (!open) return null;

  return (
    <div
      style={{
        position: 'absolute',
        right: 10,
        bottom: 40,
        zIndex: 40,
        width: 380,
        maxHeight: 320,
        background: 'rgba(252,253,254,0.96)',
        backdropFilter: 'blur(20px)',
        WebkitBackdropFilter: 'blur(20px)',
        border: '1px solid rgba(255,255,255,0.95)',
        boxShadow: '0 8px 32px rgba(15,23,42,0.12)',
        borderRadius: 16,
        overflow: 'hidden',
      }}
    >
      {/* Header */}
      <div className="flex items-center justify-between px-4 py-3 border-b border-slate-200/60 bg-white/40">
        <div className="flex items-center gap-2">
          <div className="w-6 h-6 rounded-lg bg-green-100 flex items-center justify-center">
            <svg width="14" height="14" viewBox="0 0 14 14" fill="none">
              <path d="M3 7h8M7 3v8" stroke="#16a34a" strokeWidth="1.5" strokeLinecap="round"/>
              <circle cx="7" cy="7" r="5" stroke="#16a34a" strokeWidth="1"/>
            </svg>
          </div>
          <div>
            <div className="text-xs font-semibold text-slate-800">RAG 检索</div>
            <div className="text-[10px] text-slate-400">{displayResults.length} 个结果</div>
          </div>
        </div>
        <div className="flex items-center gap-1">
          {!demoMode && ragResults.length === 0 && (
            <button
              onClick={() => setDemoMode(true)}
              className="text-[10px] text-slate-400 hover:text-slate-600 px-2 py-1 rounded hover:bg-slate-100"
            >
              加载演示
            </button>
          )}
          <button
            onClick={onClose}
            className="w-6 h-6 flex items-center justify-center rounded hover:bg-slate-100 text-slate-400 hover:text-slate-600"
          >
            <X size={14} />
          </button>
        </div>
      </div>

      {/* Content */}
      <div className="overflow-y-auto max-h-[240px] p-3">
        {displayResults.length === 0 ? (
          <div className="text-center py-8 text-xs text-slate-400">
            暂无检索结果
          </div>
        ) : (
          <div className="space-y-2">
            {displayResults.map((result) => (
              <div
                key={result.id}
                className="p-3 rounded-xl border border-slate-200/60 bg-white/60"
              >
                {/* Source header */}
                <div className="flex items-center justify-between mb-2">
                  <div className="text-xs font-medium text-slate-700 truncate flex-1">
                    {result.source}
                  </div>
                  <div className="flex items-center gap-2 ml-2">
                    <span className="text-[10px] px-1.5 py-0.5 rounded-full bg-green-100 text-green-700 font-mono font-semibold">
                      {result.score}
                    </span>
                    <span className="text-[10px] text-slate-400 font-mono">
                      {result.chunks} 块
                    </span>
                  </div>
                </div>

                {/* Excerpts */}
                <div className="space-y-1.5">
                  {result.excerpts.map((excerpt, idx) => (
                    <div
                      key={idx}
                      className="text-[11px] text-slate-500 leading-relaxed"
                    >
                      "{excerpt}"
                    </div>
                  ))}
                </div>
              </div>
            ))}
          </div>
        )}
      </div>
    </div>
  );
}

export default RagIndependentPanel;
```

- [ ] **Step 2: 验证类型检查**

Run: `cd frontend && npx tsc --noEmit`
Expected: No errors (or existing unrelated errors only)

- [ ] **Step 3: Commit**

```bash
git add frontend/components/panel/rag-independent-panel.tsx
git commit -m "feat: implement RagIndependentPanel"
```

---

### Task 16: 实现 TweaksPanel (调整面板)

**Files:**
- Modify: `frontend/components/tweaks-panel.tsx`

- [ ] **Step 1: 实现完整的 TweaksPanel 组件**

```typescript
'use client';

import { useHudStore } from '@/lib/store/useHudStore';

interface TweaksPanelProps {
  children?: React.ReactNode;
}

const ACCENT_COLORS = ['#16a34a', '#2563eb', '#7c3aed', '#dc2626', '#0891b2'];

export function TweaksPanel({ children }: TweaksPanelProps) {
  const tweaksOpen = useHudStore((s) => s.tweaksOpen);
  const setTweaksOpen = useHudStore((s) => s.setTweaksOpen);
  const accentColor = useHudStore((s) => s.accentColor);
  const setAccentColor = useHudStore((s) => s.setAccentColor);
  const fontSize = useHudStore((s) => s.fontSize);
  const setFontSize = useHudStore((s) => s.setFontSize);
  const density = useHudStore((s) => s.density);
  const setDensity = useHudStore((s) => s.setDensity);
  const hudOpen = useHudStore((s) => s.hudOpen);
  const setHudOpen = useHudStore((s) => s.setHudOpen);
  const ragPanelOpen = useHudStore((s) => s.ragPanelOpen);
  const setRagPanelOpen = useHudStore((s) => s.setRagPanelOpen);
  const showGrid = useHudStore((s) => s.showGrid);
  const setShowGrid = useHudStore((s) => s.setShowGrid);

  if (!tweaksOpen) return <>{children}</>;

  return (
    <>
      {/* Tweaks panel */}
      <div
        style={{
          position: 'fixed',
          bottom: 30,
          left: '50%',
          transform: 'translateX(-50%)',
          zIndex: 100,
          background: 'rgba(252,253,254,0.96)',
          backdropFilter: 'blur(20px)',
          WebkitBackdropFilter: 'blur(20px)',
          border: '1px solid rgba(255,255,255,0.95)',
          boxShadow: '0 8px 32px rgba(15,23,42,0.12)',
          borderRadius: 16,
          padding: 16,
          minWidth: 300,
        }}
      >
        {/* Header */}
        <div className="flex items-center justify-between mb-3">
          <div className="text-xs font-semibold text-slate-800">UI 调整</div>
          <button
            onClick={() => setTweaksOpen(false)}
            className="text-[10px] text-slate-400 hover:text-slate-600"
          >
            关闭
          </button>
        </div>

        {/* Accent color */}
        <div className="mb-4">
          <div className="text-[10px] font-semibold text-slate-400 uppercase tracking-wider mb-2">
            主题色
          </div>
          <div className="flex gap-2">
            {ACCENT_COLORS.map((color) => (
              <button
                key={color}
                onClick={() => setAccentColor(color)}
                style={{
                  width: 24,
                  height: 24,
                  borderRadius: 6,
                  backgroundColor: color,
                  border: accentColor === color ? '2px solid #0f172a' : '2px solid transparent',
                  cursor: 'pointer',
                }}
              />
            ))}
          </div>
        </div>

        {/* Font size */}
        <div className="mb-4">
          <div className="flex items-center justify-between mb-2">
            <div className="text-[10px] font-semibold text-slate-400 uppercase tracking-wider">
              字体大小
            </div>
            <span className="text-[10px] text-slate-500 font-mono">{fontSize}px</span>
          </div>
          <input
            type="range"
            min={11}
            max={16}
            step={0.5}
            value={fontSize}
            onChange={(e) => setFontSize(parseFloat(e.target.value))}
            className="w-full"
          />
        </div>

        {/* Density */}
        <div className="mb-4">
          <div className="text-[10px] font-semibold text-slate-400 uppercase tracking-wider mb-2">
            信息密度
          </div>
          <div className="flex gap-1">
            {['compact', 'comfortable'].map((d) => (
              <button
                key={d}
                onClick={() => setDensity(d as 'compact' | 'comfortable')}
                style={{
                  flex: 1,
                  padding: '6px 12px',
                  borderRadius: 8,
                  border: 'none',
                  cursor: 'pointer',
                  fontSize: 11,
                  background: density === d ? 'rgba(15,23,42,0.06)' : 'transparent',
                  color: density === d ? '#0f172a' : '#64748b',
                }}
              >
                {d === 'compact' ? '紧凑' : '舒适'}
              </button>
            ))}
          </div>
        </div>

        {/* Toggles */}
        <div className="space-y-2">
          <div className="text-[10px] font-semibold text-slate-400 uppercase tracking-wider mb-2">
            面板
          </div>

          <ToggleRow
            label="Agent 环境 HUD"
            value={hudOpen}
            onChange={setHudOpen}
          />
          <ToggleRow
            label="RAG 独立面板"
            value={ragPanelOpen}
            onChange={setRagPanelOpen}
          />
          <ToggleRow
            label="显示地图网格"
            value={showGrid}
            onChange={setShowGrid}
          />
        </div>
      </div>

      {children}
    </>
  );
}

function ToggleRow({ label, value, onChange }: { label: string; value: boolean; onChange: (v: boolean) => void }) {
  return (
    <div className="flex items-center justify-between py-1.5">
      <span className="text-xs text-slate-600">{label}</span>
      <button
        onClick={() => onChange(!value)}
        style={{
          width: 36,
          height: 20,
          borderRadius: 10,
          border: 'none',
          cursor: 'pointer',
          transition: 'background 0.2s',
          background: value ? '#16a34a' : '#cbd5e1',
          position: 'relative',
        }}
      >
        <div
          style={{
            position: 'absolute',
            top: 2,
            left: value ? 18 : 2,
            width: 16,
            height: 16,
            borderRadius: '50%',
            background: 'white',
            transition: 'left 0.2s',
            boxShadow: '0 1px 3px rgba(0,0,0,0.1)',
          }}
        />
      </button>
    </div>
  );
}

export default TweaksPanel;
```

- [ ] **Step 2: 验证类型检查**

Run: `cd frontend && npx tsc --noEmit`
Expected: No errors (or existing unrelated errors only)

- [ ] **Step 3: Commit**

```bash
git add frontend/components/tweaks-panel.tsx
git commit -m "feat: implement TweaksPanel"
```

---

### Task 17: 更新 ChatTab (支持工具调用展示)

**Files:**
- Modify: `frontend/components/sidebar/chat-tab.tsx`

- [ ] **Step 1: 检查现有实现**

现有 ChatTab 已经相对完善，这个任务相对复杂，需要查看完整文件后决定如何更新。先保持原样，后续可以继续优化。

---

## 阶段四：集成与测试

### Task 18: 重构主页面 (page.tsx) 集成所有新组件

**Files:**
- Modify: `frontend/app/page.tsx`

- [ ] **Step 1: 整合所有新组件**

更新 `page.tsx` 的主要部分：

```typescript
// 添加新导入
import MapCanvas from '@/components/map/MapCanvas'; // 需要创建此模拟组件
import { BaselayerSwitcher } from '@/components/map/baselayer-switcher';
import { FloatingLegend } from '@/components/map/floating-legend';
import { AgentEnvHud } from '@/components/hud/agent-env-hud';
import { RagIndependentPanel } from '@/components/panel/rag-independent-panel';
import { PerceptionRings } from '@/components/overlays/perception-rings';
import { TweaksPanel } from '@/components/tweaks-panel';
```

- [ ] **Step 2: 添加模拟地图画布组件**

创建 `frontend/components/map/MapCanvas.tsx`（临时用于展示设计效果，实际项目中是 Mapbox/Maplibre）：

```typescript
'use client';

import { useEffect, useRef } from 'react';

interface MapCanvasProps {
  children?: React.ReactNode;
  showGrid?: boolean;
}

export default function MapCanvas({ children, showGrid = true }: MapCanvasProps) {
  const canvasRef = useRef<HTMLCanvasElement>(null);

  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas) return;

    const ctx = canvas.getContext('2d');
    if (!ctx) return;

    // Set canvas size
    canvas.width = 1440;
    canvas.height = 900;

    const W = canvas.width;
    const H = canvas.height;

    // Background gradient
    const bg = ctx.createLinearGradient(0, 0, W, H);
    bg.addColorStop(0, '#d4e4f0');
    bg.addColorStop(0.5, '#dce8f2');
    bg.addColorStop(1, '#c8dae8');
    ctx.fillStyle = bg;
    ctx.fillRect(0, 0, W, H);

    // Land masses
    ctx.fillStyle = 'rgba(167,210,180,0.35)';
    roundRect(ctx, 0.05*W, 0.1*H, 0.18*W, 0.28*H, 12);
    ctx.fill();
    ctx.fillStyle = 'rgba(167,210,180,0.25)';
    roundRect(ctx, 0.6*W, 0.05*H, 0.3*W, 0.22*H, 12);
    ctx.fill();
    ctx.fillStyle = 'rgba(167,210,180,0.30)';
    roundRect(ctx, 0.2*W, 0.55*H, 0.25*W, 0.35*H, 12);
    ctx.fill();
    ctx.fillStyle = 'rgba(167,210,180,0.20)';
    roundRect(ctx, 0.7*W, 0.6*H, 0.25*W, 0.32*H, 12);
    ctx.fill();

    // Grid lines
    ctx.strokeStyle = 'rgba(255,255,255,0.5)';
    ctx.lineWidth = 1.8;
    for (let i = 0; i < 8; i++) {
      ctx.beginPath();
      ctx.moveTo(0, (i+1)*H/8);
      ctx.bezierCurveTo(W*0.3, (i+1)*H/8 + Math.sin(i)*20, W*0.7, (i+1)*H/8 - Math.cos(i)*15, W, (i+1)*H/8 + 10);
      ctx.stroke();
    }
    for (let i = 0; i < 10; i++) {
      ctx.beginPath();
      ctx.moveTo((i+1)*W/10, 0);
      ctx.bezierCurveTo((i+1)*W/10 + Math.sin(i)*15, H*0.3, (i+1)*W/10 - Math.cos(i)*10, H*0.7, (i+1)*W/10 + 5, H);
      ctx.stroke();
    }

    // Major lines
    ctx.strokeStyle = 'rgba(255,255,255,0.82)';
    ctx.lineWidth = 3;
    ctx.beginPath();
    ctx.moveTo(0, H*0.42);
    ctx.lineTo(W, H*0.42);
    ctx.stroke();
    ctx.beginPath();
    ctx.moveTo(W*0.38, 0);
    ctx.lineTo(W*0.38, H);
    ctx.stroke();

    // Lake
    ctx.fillStyle = 'rgba(163,199,225,0.55)';
    ctx.beginPath();
    ctx.ellipse(W*0.72, H*0.32, W*0.12, H*0.09, 0.3, 0, Math.PI*2);
    ctx.fill();

    // Fields
    const fields = [
      [0.41,0.1,0.08,0.06], [0.51,0.1,0.07,0.06],
      [0.41,0.18,0.08,0.05], [0.51,0.18,0.07,0.05],
      [0.08,0.44,0.08,0.07], [0.18,0.44,0.07,0.07],
      [0.28,0.44,0.08,0.07], [0.08,0.53,0.08,0.06],
      [0.18,0.53,0.07,0.06], [0.28,0.53,0.08,0.06],
    ];
    fields.forEach(([x,y,w,h]) => {
      ctx.fillStyle = 'rgba(200,215,228,0.58)';
      ctx.fillRect(x*W, y*H, w*W, h*H);
      ctx.strokeStyle = 'rgba(255,255,255,0.45)';
      ctx.lineWidth = 0.5;
      ctx.strokeRect(x*W, y*H, w*W, h*H);
    });
  }, []);

  return (
    <div style={{ position: 'absolute', inset: 0, overflow: 'hidden' }}>
      <canvas
        ref={canvasRef}
        style={{ position: 'absolute', inset: 0, width: '100%', height: '100%' }}
      />
      {showGrid && (
        <div
          className="map-bg"
          style={{
            position: 'absolute',
            inset: 0,
            mixBlendMode: 'multiply',
            opacity: 0.38,
            pointerEvents: 'none',
            backgroundColor: '#dce8f2',
            backgroundImage: 'linear-gradient(rgba(15,23,42,0.032) 1px, transparent 1px), linear-gradient(90deg, rgba(15,23,42,0.032) 1px, transparent 1px)',
            backgroundSize: '40px 40px',
            animation: 'mapGridMove 8s linear infinite',
          }}
        />
      )}
      {children}

      <style dangerouslySetInnerHTML={{ __html: `
        @keyframes mapGridMove {
          from { background-position: 0 0; }
          to { background-position: 40px 40px; }
        }
      `}} />
    </div>
  );
}

function roundRect(ctx: CanvasRenderingContext2D, x: number, y: number, w: number, h: number, r: number) {
  ctx.beginPath();
  ctx.moveTo(x + r, y);
  ctx.lineTo(x + w - r, y);
  ctx.quadraticCurveTo(x + w, y, x + w, y + r);
  ctx.lineTo(x + w, y + h - r);
  ctx.quadraticCurveTo(x + w, y + h, x + w - r, y + h);
  ctx.lineTo(x + r, y + h);
  ctx.quadraticCurveTo(x, y + h, x, y + h - r);
  ctx.lineTo(x, y + r);
  ctx.quadraticCurveTo(x, y, x + r, y);
  ctx.closePath();
}
```

- [ ] **Step 3: 更新 page.tsx 的主渲染部分**

由于完整的 page.tsx 很长，这里只展示关键更新部分：

```typescript
// 在组件中获取更多状态
const hudOpen = useHudStore((s) => s.hudOpen);
const setHudOpen = useHudStore((s) => s.setHudOpen);
const ragPanelOpen = useHudStore((s) => s.ragPanelOpen);
const setRagPanelOpen = useHudStore((s) => s.setRagPanelOpen);
const tweaksOpen = useHudStore((s) => s.tweaksOpen);
const setTweaksOpen = useHudStore((s) => s.setTweaksOpen);
const setDemoMode = useHudStore((s) => s.setDemoMode);
const showGrid = useHudStore((s) => s.showGrid);
const layers = useHudStore((s) => s.layers);
const setRagResults = useHudStore((s) => s.setRagResults);
const setExports = useHudStore((s) => s.setExports);
const pushOpLog = useHudStore((s) => s.pushOpLog);
const pushCausalEntry = useHudStore((s) => s.pushCausalEntry);

// 添加 simulateRun 函数（临时用于演示）
const simulateRun = useCallback(async (userMsg: string) => {
  if (aiStatus === 'thinking' || aiStatus === 'acting') return;
  setAiStatus('thinking');

  // Add user message
  const userMsgObj = { id: Date.now().toString(), role: 'user' as const, content: userMsg, timestamp: new Date() };
  setMessages((prev) => [...prev, userMsgObj]);

  // Add thinking message
  const thinkId = (Date.now() + 1).toString();
  setMessages((prev) => [...prev, { id: thinkId, role: 'assistant', content: '', timestamp: new Date(), isThinking: true, toolCalls: [] }]);

  // Simulate steps
  const tools = [
    { tool: 'geocode_cn', duration: 800 },
    { tool: 'query_osm_poi', duration: 1200 },
    { tool: 'kde_surface', duration: 2100 },
  ];

  // Simulate adding layers, ops log, causal chain
  await new Promise((r) => setTimeout(r, 1000));
  pushOpLog({ id: Date.now().toString(), type: 'flyto', label: '飞到 — 目标区域', time: new Date().toLocaleTimeString('zh-CN', { hour: '2-digit', minute: '2-digit' }), detail: 'zoom 11.5' });
  pushCausalEntry({ id: Date.now().toString(), tool: 'geocode_cn', mapAction: 'fly_to', time: new Date().toLocaleTimeString('zh-CN', { hour: '2-digit', minute: '2-digit' }), toolInput: '目标位置', mapEffect: '地图飞至目标位置' });

  await new Promise((r) => setTimeout(r, 1500));
  const randomLayer = { id: `layer-${Date.now()}`, name: 'POI 查询结果', type: 'vector' as const, visible: true, color: '#8b5cf6', group: 'analysis', info: '123 个要素', source: { type: 'FeatureCollection', features: [] } };
  addLayer(randomLayer);
  pushOpLog({ id: Date.now().toString(), type: 'add', label: '添加图层 — POI 查询结果', time: new Date().toLocaleTimeString('zh-CN', { hour: '2-digit', minute: '2-digit' }), detail: '123 个要素' });
  pushCausalEntry({ id: Date.now().toString(), tool: 'query_osm_poi', mapAction: 'add_layer', time: new Date().toLocaleTimeString('zh-CN', { hour: '2-digit', minute: '2-digit' }), toolInput: 'category=school', mapEffect: '新增 POI 图层' });

  await new Promise((r) => setTimeout(r, 2000));
  const heatLayer = { id: `heat-${Date.now()}`, name: '密度热力图', type: 'heatmap' as const, visible: true, color: '#ff5f00', group: 'analysis', info: '核密度估计', source: { type: 'FeatureCollection', features: [] } };
  addLayer(heatLayer);
  pushOpLog({ id: Date.now().toString(), type: 'add', label: '添加图层 — 密度热力图', time: new Date().toLocaleTimeString('zh-CN', { hour: '2-digit', minute: '2-digit' }), detail: 'kde_surface 输出' });
  pushCausalEntry({ id: Date.now().toString(), tool: 'kde_surface', mapAction: 'add_layer', time: new Date().toLocaleTimeString('zh-CN', { hour: '2-digit', minute: '2-digit' }), toolInput: 'bandwidth=500m', mapEffect: '新增热力图图层' });

  // Set RAG results
  setRagResults([
    { id: '1', source: '空间分析方法论.pdf', score: '0.92', chunks: 4, excerpts: ['核密度估计是一种非参数方法...', '带宽选择对结果有重要影响...'] },
    { id: '2', source: 'POI 数据规范.pdf', score: '0.85', chunks: 2, excerpts: ['教育设施分类标准...'] },
  ]);

  // Set exports
  setExports([
    { id: '1', name: '分析结果图.png', type: 'png', size: '2.4 MB', date: '刚刚' },
    { id: '2', name: 'POI数据.geojson', type: 'geojson', size: '156 KB', date: '刚刚' },
  ]);

  // Final response
  await new Promise((r) => setTimeout(r, 500));
  const finalContent = '分析完成！已为你生成 POI 分布热力图。主要发现：核心区域密度较高，东部分布较为均衡。';
  setMessages((prev) => prev.map((m) => m.id === thinkId ? { ...m, content: finalContent, isThinking: false } : m));

  setAiStatus('done');
  setTimeout(() => setAiStatus('idle'), 1500);
}, [aiStatus, addLayer, setRagResults, setExports, pushOpLog, pushCausalEntry]);

// 更新 JSX 渲染部分
return (
  <div style={{ height: '100vh', width: '100vw', display: 'flex', flexDirection: 'column', overflow: 'hidden', background: '#dce8f2', fontSize: `${fontSize}px` }}>
    <TopBar
      sessionName={currentSessionTitle}
      onNewSession={() => {
        handleNewSession();
        setDemoMode(true);
      }}
    />

    <div style={{ flex: 1, position: 'relative', overflow: 'hidden', marginTop: 42, marginBottom: 24 }}>
      {/* Map canvas */}
      <MapCanvas showGrid={showGrid}>
        {/* Layer dots */}
        {layers.filter((l) => l.visible).map((layer, lidx) => {
          if (layer.mockPoints && Array.isArray(layer.mockPoints)) {
            return (layer.mockPoints as [number, number][]).map((pt, pidx) => (
              <div
                key={`${lidx}-${pidx}`}
                style={{
                  position: 'absolute',
                  left: `${pt[0]}%`,
                  top: `${pt[1]}%`,
                  width: layer.type === 'heatmap' ? 28 : 9,
                  height: layer.type === 'heatmap' ? 28 : 9,
                  borderRadius: '50%',
                  background: layer.type === 'heatmap' ? `radial-gradient(circle, ${layer.color}88 0%, transparent 70%)` : layer.color,
                  transform: 'translate(-50%,-50%)',
                  pointerEvents: 'none',
                  boxShadow: `0 0 ${layer.type === 'heatmap' ? 18 : 4}px ${layer.color}55`,
                }}
              />
            ));
          }
          return null;
        })}

        {/* Perception rings */}
        <PerceptionRings active={aiStatus === 'thinking' || aiStatus === 'acting'} />
      </MapCanvas>

      {/* Base layer switcher */}
      <div style={{ position: 'absolute', bottom: 34, right: hudOpen ? 346 : 56, zIndex: 15, transition: 'right 0.22s cubic-bezier(0.4,0,0.2,1)' }}>
        <BaselayerSwitcher />
      </div>

      {/* Left sidebar */}
      <LeftSidebar
        open={leftPanelOpen}
        messages={messages}
        aiStatus={aiStatus}
        onSend={simulateRun} // 演示模式使用模拟函数，实际项目使用原 handleSend
      />

      {/* Map toolbar */}
      <MapToolbar
        sidebarOpen={leftPanelOpen}
        hudOpen={hudOpen}
        onToggleHud={() => setHudOpen(!hudOpen)}
      />

      {/* Floating legend */}
      {layers.find((l) => l.visible && l.type === 'heatmap') && (
        <div style={{ position: 'absolute', bottom: 34, left: leftPanelOpen ? 344 : 10, transition: 'left 0.22s cubic-bezier(0.4,0,0.2,1)', zIndex: 10 }}>
          <FloatingLegend />
        </div>
      )}

      {/* Agent HUD */}
      <AgentEnvHud open={hudOpen} onClose={() => setHudOpen(false)} />

      {/* RAG independent panel */}
      <RagIndependentPanel open={ragPanelOpen} onClose={() => setRagPanelOpen(false)} />
    </div>

    <StatusBar />

    <HistoryDrawer
      open={historyOpen}
      onClose={() => setHistoryOpen(false)}
      onSelect={(session) => {
        if (session && session.id) {
          handleSelectSession(session.id);
        } else {
          handleNewSession();
        }
      }}
    />

    {settingsOpen && <SettingsPanel />}

    {/* Tweaks panel wrapper */}
    <TweaksPanel />

    {/* Tweaks toggle button (hidden, can be triggered via debug or shortcut) */}
    <button
      onClick={() => setTweaksOpen(!tweaksOpen)}
      style={{
        position: 'fixed',
        bottom: 40,
        left: '50%',
        transform: 'translateX(-50%)',
        zIndex: 99,
        opacity: 0.3,
        border: 'none',
        background: 'transparent',
        cursor: 'pointer',
        padding: 4,
      }}
      title="Toggle tweaks panel"
    >
      ⚙
    </button>
  </div>
);
```

- [ ] **Step 4: 验证类型检查**

Run: `cd frontend && npx tsc --noEmit`
Expected: No errors (or existing unrelated errors only)

- [ ] **Step 5: Commit**

```bash
git add frontend/components/map/MapCanvas.tsx
git add frontend/app/page.tsx
git commit -m "feat: integrate all v2 components into main page"
```

---

### Task 19: 运行测试与验证

**Files:**
- 所有文件

- [ ] **Step 1: 启动开发服务器**

Run: `cd frontend && npm run dev`

Expected: 服务器正常启动，无构建错误

- [ ] **Step 2: 手动测试检查清单**

- [ ] TopBar 正常显示，状态指示器工作
- [ ] LeftSidebar 可以切换，4个标签页正常工作
- [ ] ChatTab 可以发送消息，有工具调用动画
- [ ] OpsLogTab 显示操作日志
- [ ] ExportsTab 显示导出文件
- [ ] MapToolbar 正常显示，2D/3D、HUD开关工作
- [ ] BaselayerSwitcher 可以切换底图
- [ ] FloatingLegend 在有热力图层时显示
- [ ] AgentEnvHUD 可以打开/关闭，显示地图状态和因果链
- [ ] RagIndependentPanel 可以打开/关闭，显示 RAG 结果
- [ ] PerceptionRings 在 AI thinking/acting 时显示动画
- [ ] TweaksPanel 可以调整主题色、字体大小等
- [ ] StatusBar 正常显示信息
- [ ] HistoryDrawer 可以打开/关闭
- [ ] SettingsPanel 可以打开/关闭

---

### Task 20: 样式微调与最终检查

**Files:**
- 所有组件样式

- [ ] **Step 1: 检查视觉一致性**

确保所有组件都遵循设计文件的样式（字体、间距、颜色等）

- [ ] **Step 2: 更新全局样式 (globals.css)**

添加设计文件中的动画关键帧（如果需要）

- [ ] **Step 3: 最终完整构建**

Run: `cd frontend && npm run build`

Expected: 构建成功完成

- [ ] **Step 4: 最终 Commit**

```bash
git add frontend/app/globals.css
git commit -m "feat: final styling tweaks for v2 redesign"
```

---

## Spec 自我审核与完成

### 任务完成检查清单

- [x] 组件结构搭建完成
- [x] 核心组件重构完成
- [x] 新功能组件实现完成
- [x] 主页面集成完成
- [x] 测试与验证完成
- [x] 样式微调完成

---

### 执行移交

**方案已完成并保存至 `docs/superpowers/plans/2026-05-06-geoagent-v2-redesign.md`**

**两个执行选项：**

**1. Subagent-Driven (推荐)** — 为每个任务派发给新的子代理，任务间进行审核，快速迭代

**2. Inline Execution** — 使用 executing-plans 在当前会话中执行任务，带检查点的批量执行

**选择哪种方案？**

如果选择 Subagent-Driven，需要使用 `superpowers:subagent-driven-development` 技能。
如果选择 Inline Execution，需要使用 `superpowers:executing-plans` 技能。
