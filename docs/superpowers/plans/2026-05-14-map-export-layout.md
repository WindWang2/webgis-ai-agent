# Professional Map Export & Layout Panel Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Implement a WYSIWYG professional map export feature, providing a sidebar panel for layout configuration (paper size, DPI, cartographic elements) and dynamic high-res Canvas compositing for PNG/PDF output.

**Architecture:** 
1. **Store:** Extend `useHudStore` to hold `exportSettings`.
2. **UI:** A new `ExportTab` in the left sidebar to configure options.
3. **Preview:** An `ExportMask` component layered over `MapPanel` to simulate aspect ratio cropping, plus absolutely positioned cartography elements (Compass, Scale, Legend) bound to export state.
4. **Renderer:** Refactor `MapActionHandler`'s `export_map` case to resize the map internally or redraw the canvas accurately taking DPI and aspect ratio into account before triggering the download.

**Tech Stack:** React, Zustand, MapLibre GL JS, Canvas API, TailwindCSS.

---

### Task 1: Store and Types Extension

**Files:**
- Modify: `frontend/lib/store/hud-types.ts`
- Modify: `frontend/lib/store/useHudStore.ts`
- Modify: `frontend/components/sidebar/left-sidebar.tsx`

- [ ] **Step 1: Update `hud-types.ts`**
  Add the `ExportSettings` interface and update `HudState` to include it and an `updateExportSettings` method. Add 'export' to the LeftSidebar tab types.

```typescript
export interface ExportSettings {
  isExportMode: boolean;
  title: string;
  subtitle: string;
  showWatermark: boolean;
  showCompass: boolean;
  showScale: boolean;
  showLegend: boolean;
  paperSize: 'screen' | 'A4';
  orientation: 'landscape' | 'portrait';
  dpi: number;
  format: 'png' | 'pdf';
}

// In HudState interface:
export interface HudState {
  // ... existing fields
  activeLeftTab: 'chat' | 'layers' | 'assets' | 'exports' | 'export_layout';
  // ... existing fields
  
  /* ─── Export Layout ─── */
  exportSettings: ExportSettings;
  updateExportSettings: (updates: Partial<ExportSettings>) => void;
}
```

- [ ] **Step 2: Update `useHudStore.ts`**
  Implement the new store fields and method.

```typescript
// Inside useHudStore create()
      /* ─── Export Layout ─── */
      exportSettings: {
        isExportMode: false,
        title: '',
        subtitle: '',
        showWatermark: true,
        showCompass: true,
        showScale: true,
        showLegend: true,
        paperSize: 'screen',
        orientation: 'landscape',
        dpi: 96,
        format: 'png',
      },
      updateExportSettings: (updates) =>
        set((s) => ({
          exportSettings: { ...s.exportSettings, ...updates },
        })),
```

- [ ] **Step 3: Add tab icon to `left-sidebar.tsx`**
  Add the new tab to the sidebar navigation so the user can open it. Add the icon to `TABS`.

```tsx
import { MessageSquare, Layers, FileDown, Database, Printer } from 'lucide-react'; // Add Printer

const TABS = [
  { id: 'chat', label: '对话', icon: MessageSquare, badge: 0 },
  { id: 'layers', label: '图层', icon: Layers, badge: 0 },
  { id: 'assets', label: '资源', icon: Database, badge: 0 },
  { id: 'export_layout', label: '制图', icon: Printer, badge: 0 },
  { id: 'exports', label: '导出', icon: FileDown, badge: 0 },
] as const;

// Further down in render, handle 'export_layout' conditionally (we will build the component next):
// {activeTab === 'export_layout' && <ExportLayoutTab />}
```

- [ ] **Step 4: Commit**
```bash
git add frontend/lib/store/hud-types.ts frontend/lib/store/useHudStore.ts frontend/components/sidebar/left-sidebar.tsx
git commit -m "feat(export): add store state for map layout settings"
```

### Task 2: Create the ExportPanel (Sidebar Tab)

**Files:**
- Create: `frontend/components/sidebar/export-layout-tab.tsx`
- Modify: `frontend/components/sidebar/left-sidebar.tsx`

- [ ] **Step 1: Create `export-layout-tab.tsx`**
  Build a form-like sidebar tab that binds to `useHudStore` exportSettings.

```tsx
import { useEffect } from 'react';
import { useHudStore } from '@/lib/store/useHudStore';
import { useMapAction } from '@/lib/contexts/map-action-context';

export function ExportLayoutTab() {
  const { exportSettings, updateExportSettings } = useHudStore(s => ({
    exportSettings: s.exportSettings,
    updateExportSettings: s.updateExportSettings
  }));
  const { dispatchAction } = useMapAction();

  // Helper to update specific fields
  const handleChange = (key: keyof typeof exportSettings, value: any) => {
    updateExportSettings({ [key]: value });
  };

  // Auto-enable export mode when tab is opened
  useEffect(() => {
    updateExportSettings({ isExportMode: true });
    return () => updateExportSettings({ isExportMode: false });
  }, [updateExportSettings]);

  return (
    <div className="flex flex-col h-full overflow-y-auto bg-slate-50">
      <div className="p-4 border-b bg-white">
        <h2 className="font-semibold text-slate-800">专题制图排版</h2>
        <p className="text-xs text-slate-500 mt-1">配置地图元素与输出格式</p>
      </div>

      <div className="p-4 space-y-6 flex-1">
        {/* Texts */}
        <div className="space-y-3">
          <label className="block text-sm font-medium text-slate-700">主标题</label>
          <input type="text" value={exportSettings.title} onChange={e => handleChange('title', e.target.value)} placeholder="如：成都市高校分布图" className="w-full text-sm border rounded px-3 py-2" />
          
          <label className="block text-sm font-medium text-slate-700 mt-2">副标题</label>
          <input type="text" value={exportSettings.subtitle} onChange={e => handleChange('subtitle', e.target.value)} placeholder="如：数据来源: OSM, 制图日期: 2026" className="w-full text-sm border rounded px-3 py-2" />
        </div>

        {/* Elements */}
        <div className="space-y-2">
          <label className="block text-sm font-medium text-slate-700">地图元素</label>
          <div className="flex items-center gap-2">
            <input type="checkbox" checked={exportSettings.showCompass} onChange={e => handleChange('showCompass', e.target.checked)} /> <span className="text-sm">指北针</span>
          </div>
          <div className="flex items-center gap-2">
            <input type="checkbox" checked={exportSettings.showScale} onChange={e => handleChange('showScale', e.target.checked)} /> <span className="text-sm">比例尺</span>
          </div>
          <div className="flex items-center gap-2">
            <input type="checkbox" checked={exportSettings.showLegend} onChange={e => handleChange('showLegend', e.target.checked)} /> <span className="text-sm">图例</span>
          </div>
          <div className="flex items-center gap-2">
            <input type="checkbox" checked={exportSettings.showWatermark} onChange={e => handleChange('showWatermark', e.target.checked)} /> <span className="text-sm">水印 (Generated by WebGIS AI)</span>
          </div>
        </div>

        {/* Paper & Quality */}
        <div className="space-y-3 border-t pt-4">
          <label className="block text-sm font-medium text-slate-700">输出设置</label>
          <select value={exportSettings.format} onChange={e => handleChange('format', e.target.value)} className="w-full text-sm border rounded px-3 py-2">
            <option value="png">PNG 图片</option>
            <option value="pdf">PDF 文档</option>
          </select>
          <select value={exportSettings.paperSize} onChange={e => handleChange('paperSize', e.target.value)} className="w-full text-sm border rounded px-3 py-2">
            <option value="screen">当前屏幕比例 (Screen)</option>
            <option value="A4">A4 纸张尺寸</option>
          </select>
          <select value={exportSettings.orientation} onChange={e => handleChange('orientation', e.target.value)} disabled={exportSettings.paperSize === 'screen'} className="w-full text-sm border rounded px-3 py-2 disabled:opacity-50">
            <option value="landscape">横向 (Landscape)</option>
            <option value="portrait">纵向 (Portrait)</option>
          </select>
          <select value={exportSettings.dpi} onChange={e => handleChange('dpi', Number(e.target.value))} className="w-full text-sm border rounded px-3 py-2">
            <option value={96}>标准清晰度 (96 DPI)</option>
            <option value={150}>高清晰度 (150 DPI)</option>
            {exportSettings.paperSize === 'screen' && <option value={300}>超清排版 (300 DPI)</option>}
          </select>
        </div>
      </div>
      
      <div className="p-4 bg-white border-t">
        <button className="w-full bg-blue-600 text-white font-medium py-2 rounded shadow hover:bg-blue-700 transition" onClick={() => {
           // We will wire this up later to useMapAction
           alert("Export pending implementation");
        }}>
          导出 {exportSettings.format.toUpperCase()}
        </button>
      </div>
    </div>
  );
}
export default ExportLayoutTab;
```

- [ ] **Step 2: Wire it into `left-sidebar.tsx`**
```tsx
import ExportLayoutTab from './export-layout-tab';

// Inside the render where tabs are switched
{activeTab === 'export_layout' && <ExportLayoutTab />}
```

- [ ] **Step 3: Commit**
```bash
git add frontend/components/sidebar/export-layout-tab.tsx frontend/components/sidebar/left-sidebar.tsx
git commit -m "feat(export): add export layout settings sidebar panel"
```

### Task 3: The WYSIWYG Export Mask Layer

**Files:**
- Create: `frontend/components/map/export-mask.tsx`
- Modify: `frontend/app/page.tsx`

- [ ] **Step 1: Create `export-mask.tsx`**
This component reads the `exportSettings` and draws a semi-transparent border (mask) over the parts of the screen that will be cropped, leaving the exact A4 (or screen) ratio transparent. It also renders the UI preview of the title and watermark.

```tsx
import { useHudStore } from '@/lib/store/useHudStore';
import { useMemo, useEffect, useState } from 'react';

export function ExportMask() {
  const settings = useHudStore(s => s.exportSettings);
  const isDark = useHudStore(s => s.theme === 'dark');
  const [containerSize, setContainerSize] = useState({ w: 0, h: 0 });

  useEffect(() => {
    const updateSize = () => setContainerSize({ w: window.innerWidth, h: window.innerHeight });
    window.addEventListener('resize', updateSize);
    updateSize();
    return () => window.removeEventListener('resize', updateSize);
  }, []);

  const maskStyle = useMemo(() => {
    if (settings.paperSize === 'screen' || containerSize.w === 0) return { display: 'none' };
    
    // A4 ratio: 1 : 1.414
    let targetRatio = settings.orientation === 'landscape' ? 1.414 : 1 / 1.414;
    
    // Calculate the maximum box fitting inside the container
    const containerRatio = containerSize.w / containerSize.h;
    let boxW = 0; let boxH = 0;
    if (containerRatio > targetRatio) {
      boxH = containerSize.h;
      boxW = boxH * targetRatio;
    } else {
      boxW = containerSize.w;
      boxH = boxW / targetRatio;
    }
    
    const padX = (containerSize.w - boxW) / 2;
    const padY = (containerSize.h - boxH) / 2;

    return {
      borderWidth: `${Math.max(0, padY)}px ${Math.max(0, padX)}px`,
      borderColor: 'rgba(0,0,0,0.6)',
      borderStyle: 'solid',
      pointerEvents: 'none' as const,
      position: 'absolute' as const,
      inset: 0,
      zIndex: 20
    };
  }, [settings.paperSize, settings.orientation, containerSize]);

  if (!settings.isExportMode) return null;

  return (
    <div style={maskStyle}>
       {/* Preview Header */}
       {(settings.title || settings.subtitle) && (
         <div className="absolute top-0 left-0 w-full p-8 pointer-events-none" style={{ background: `linear-gradient(to bottom, ${isDark ? 'rgba(0,10,20,0.8)' : 'rgba(255,255,255,0.9)'}, transparent)` }}>
            {settings.title && <h1 className={`text-3xl font-bold ${isDark ? 'text-[#00f2ff]' : 'text-slate-800'}`}>{settings.title}</h1>}
            {settings.subtitle && <h2 className={`text-xl mt-2 ${isDark ? 'text-slate-300' : 'text-slate-600'}`}>{settings.subtitle}</h2>}
         </div>
       )}
       {/* Preview Watermark */}
       {settings.showWatermark && (
         <div className="absolute bottom-4 right-4 pointer-events-none opacity-50 text-sm font-mono font-bold" style={{ color: isDark ? '#fff' : '#000' }}>
           Generated by WebGIS AI Agent
         </div>
       )}
    </div>
  );
}
```

- [ ] **Step 2: Inject into `page.tsx`**
```tsx
import { ExportMask } from '@/components/map/export-mask';

// Inside Home component render, just after the MapPanel wrapper
        {/* Map Panel */}
        <div style={{ position: 'absolute', inset: 0 }}>
          <MapPanel
             // ...
          />
          <ExportMask />
        </div>
```

- [ ] **Step 3: Commit**
```bash
git add frontend/components/map/export-mask.tsx frontend/app/page.tsx
git commit -m "feat(export): add WYSIWYG crop mask and layout preview decorators"
```

### Task 4: Refactor MapActionHandler Export Logic

**Files:**
- Modify: `frontend/components/map/map-action-handler.tsx`
- Modify: `frontend/components/sidebar/export-layout-tab.tsx`

- [ ] **Step 1: Wire up the Export button in `export-layout-tab.tsx`**
Use `useMapAction` to dispatch the command with current settings.
```tsx
import { useMapAction } from '@/lib/contexts/map-action-context';
// ... inside ExportLayoutTab component:
  const { dispatchAction } = useMapAction();
// ...
        <button className="w-full bg-blue-600 text-white font-medium py-2 rounded shadow hover:bg-blue-700 transition" onClick={() => {
           dispatchAction({
             command: 'export_map',
             params: { ...exportSettings }
           });
        }}>
```

- [ ] **Step 2: Update `map-action-handler.tsx` canvas composer**
Modify the `export_map` case to read `action.params.dpi` and `paperSize`, scaling the Canvas elements correctly. If the user asks for A4, we calculate aspect ratio crops on the resulting canvas image.
*Note: Due to MapLibre WebGL limits, we can't easily resize the live WebGL canvas dynamically without massive flicker. Instead, we use `map.getCanvas()` as the base, compute crop boundaries, and scale the text/overlays based on the requested DPI factor.*

```tsx
// Inside MapActionHandler export_map case:
          const {
            title,
            subtitle,
            showWatermark = true,
            showLegend = true,
            showCompass = true,
            showScale = true,
            format = "png",
            paperSize = "screen",
            orientation = "landscape",
            dpi = 96
          } = action.params;
          
          const dark_mode = useHudStore.getState().theme === 'dark';

          map.once("render", async () => {
            try {
              const baseCanvas = map.getCanvas();
              let srcW = baseCanvas.width;
              let srcH = baseCanvas.height;
              let srcX = 0;
              let srcY = 0;

              // 1. Calculate Crop Box if A4
              if (paperSize === 'A4') {
                const targetRatio = orientation === 'landscape' ? 1.414 : 1 / 1.414;
                const canvasRatio = srcW / srcH;
                
                if (canvasRatio > targetRatio) {
                  const newW = srcH * targetRatio;
                  srcX = (srcW - newW) / 2;
                  srcW = newW;
                } else {
                  const newH = srcW / targetRatio;
                  srcY = (srcH - newH) / 2;
                  srcH = newH;
                }
              }

              // 2. High-DPI Upscaling calculation
              // Base MapLibre is usually 96dpi * window.devicePixelRatio
              const dpiMultiplier = dpi / 96;
              const targetW = Math.round(srcW * dpiMultiplier);
              const targetH = Math.round(srcH * dpiMultiplier);

              const exportCanvas = document.createElement("canvas");
              exportCanvas.width = targetW;
              exportCanvas.height = targetH;
              const ctx = exportCanvas.getContext("2d");
              if (!ctx) return;

              // Draw cropped base map
              ctx.drawImage(baseCanvas, srcX, srcY, srcW, srcH, 0, 0, targetW, targetH);

              // 3. Draw Overlays (scaled by dpiMultiplier)
              const scalePx = (val: number) => val * dpiMultiplier;

              // Header gradient
              const headerH = subtitle ? scalePx(130) : scalePx(100);
              const headerGrad = ctx.createLinearGradient(0, 0, 0, headerH);
              headerGrad.addColorStop(0, dark_mode ? "rgba(0,10,20,0.88)" : "rgba(255,255,255,0.96)");
              headerGrad.addColorStop(0.65, dark_mode ? "rgba(0,10,20,0.45)" : "rgba(255,255,255,0.55)");
              headerGrad.addColorStop(1, "rgba(0,0,0,0)");
              ctx.fillStyle = headerGrad;
              ctx.fillRect(0, 0, targetW, headerH);

              // Title
              ctx.fillStyle = dark_mode ? "#00f2ff" : "#1e293b";
              ctx.font = `bold ${scalePx(32)}px sans-serif`;
              ctx.fillText(title || "WebGIS AI Agent", scalePx(56), scalePx(52));

              if (subtitle) {
                ctx.fillStyle = dark_mode ? "rgba(255,255,255,0.72)" : "rgba(30,41,59,0.72)";
                ctx.font = `${scalePx(20)}px sans-serif`;
                ctx.fillText(subtitle, scalePx(56), scalePx(82));
              }

              // ── 2. Scale bar ──────────────────────────────────────────────
              if (showScale) {
                const center = map.getCenter();
                const zoom = map.getZoom();
                const metersPerPx =
                  (156543.03392 * Math.cos((center.lat * Math.PI) / 180)) /
                  Math.pow(2, zoom);
                const targetPx = Math.round(srcW * 0.12);
                const rawMeters = metersPerPx * targetPx;
                const magnitude = Math.pow(10, Math.floor(Math.log10(rawMeters)));
                const nice = [1, 2, 5, 10].reduce((prev, n) => {
                  const candidate = n * magnitude;
                  return Math.abs(candidate - rawMeters) < Math.abs(prev - rawMeters)
                    ? candidate
                    : prev;
                }, magnitude);
                const barPx = (nice / metersPerPx) * dpiMultiplier;
                const barLabel = nice >= 1000 ? `${nice / 1000} km` : `${nice} m`;

                const bx = scalePx(56), by = targetH - scalePx(52), bh = scalePx(8);
                ctx.strokeStyle = dark_mode ? "rgba(255,255,255,0.9)" : "rgba(0,0,0,0.8)";
                ctx.lineWidth = scalePx(1.5);
                ctx.strokeRect(bx, by, barPx, bh);
                const segCount = 4;
                const segW = barPx / segCount;
                for (let i = 0; i < segCount; i++) {
                  ctx.fillStyle =
                    i % 2 === 0
                      ? dark_mode ? "rgba(255,255,255,0.9)" : "rgba(0,0,0,0.8)"
                      : "rgba(0,0,0,0)";
                  ctx.fillRect(bx + i * segW, by, segW, bh);
                }
                ctx.fillStyle = dark_mode ? "rgba(255,255,255,0.95)" : "#1e293b";
                ctx.font = `bold ${scalePx(13)}px sans-serif`;
                ctx.textAlign = "left";
                ctx.fillText("0", bx, by - scalePx(4));
                ctx.textAlign = "right";
                ctx.fillText(barLabel, bx + barPx, by - scalePx(4));
                ctx.textAlign = "left";
              }

              // ── 3. North arrow (compass) ─────────────────────────────────
              if (showCompass) {
                const bearing = map.getBearing();
                const cx = targetW - scalePx(64), cy = scalePx(64), r = scalePx(28);
                ctx.save();
                ctx.translate(cx, cy);
                ctx.rotate((bearing * Math.PI) / 180);
                ctx.shadowColor = "rgba(0,0,0,0.4)";
                ctx.shadowBlur = scalePx(6);
                ctx.beginPath();
                ctx.moveTo(0, -r);
                ctx.lineTo(r * 0.35, 0);
                ctx.lineTo(0, r * 0.2);
                ctx.lineTo(-r * 0.35, 0);
                ctx.closePath();
                ctx.fillStyle = "#e53e3e";
                ctx.fill();
                ctx.beginPath();
                ctx.moveTo(0, r);
                ctx.lineTo(r * 0.35, 0);
                ctx.lineTo(0, r * 0.2);
                ctx.lineTo(-r * 0.35, 0);
                ctx.closePath();
                ctx.fillStyle = dark_mode ? "rgba(255,255,255,0.9)" : "#f8fafc";
                ctx.fill();
                ctx.shadowBlur = 0;
                ctx.beginPath();
                ctx.arc(0, 0, scalePx(4), 0, 2 * Math.PI);
                ctx.fillStyle = "#1e293b";
                ctx.fill();
                ctx.restore();
                ctx.fillStyle = dark_mode ? "rgba(255,255,255,0.95)" : "#1e293b";
                ctx.font = `bold ${scalePx(13)}px sans-serif`;
                ctx.textAlign = "center";
                ctx.fillText("N", cx, cy - r - scalePx(6));
                ctx.textAlign = "left";
              }

              // ── 4. Legend (choropleth only) ───────────────────────────────
              if (showLegend) {
                const COLOR_PALETTES: Record<string, string[]> = {
                  YlOrRd: ["#ffffb2","#fed976","#feb24c","#fd8d3c","#f03b20","#bd0026"],
                  Blues:  ["#eff3ff","#bdd7e7","#6baed6","#3182bd","#08519c"],
                };
                const storeState = useHudStore.getState();
                const thematicLayer = storeState.layers.find(
                  (l) => l.visible && (l.source as any)?.metadata?.thematic_type === "choropleth"
                );
                if (thematicLayer) {
                  const meta = (thematicLayer.source as any).metadata;
                  const colors = COLOR_PALETTES[meta.palette] ?? COLOR_PALETTES["YlOrRd"];
                  const classes = meta.breaks.length - 1;
                  const itemH = scalePx(22), itemW = scalePx(18), padding = scalePx(10), gapX = scalePx(8);
                  const legendW = scalePx(180);
                  const legendH = padding * 2 + scalePx(24) + classes * itemH;
                  const lx = targetW - legendW - scalePx(56);
                  const ly = targetH - legendH - scalePx(56);
                  ctx.fillStyle = dark_mode ? "rgba(0,10,20,0.82)" : "rgba(255,255,255,0.88)";
                  ctx.fillRect(lx, ly, legendW, legendH);
                  ctx.fillStyle = dark_mode ? "#00f2ff" : "#1e293b";
                  ctx.font = `bold ${scalePx(12)}px sans-serif`;
                  ctx.fillText(`字段: ${meta.field}`, lx + padding, ly + padding + scalePx(12));
                  for (let i = 0; i < classes; i++) {
                    const iy = ly + padding + scalePx(24) + i * itemH;
                    const colorIdx = Math.min(i, colors.length - 1);
                    ctx.fillStyle = colors[colorIdx];
                    ctx.fillRect(lx + padding, iy, itemW, itemH - scalePx(4));
                    ctx.fillStyle = dark_mode ? "rgba(255,255,255,0.85)" : "#334155";
                    ctx.font = `${scalePx(11)}px sans-serif`;
                    ctx.fillText(`${meta.breaks[i]} – ${meta.breaks[i + 1]}`, lx + padding + itemW + gapX, iy + itemH - scalePx(8));
                  }
                }
              }

              // ── 5. Watermark ─────────────────────────────────────────────
              if (showWatermark) {
                ctx.fillStyle = dark_mode ? "rgba(255,255,255,0.5)" : "rgba(0,0,0,0.4)";
                ctx.textAlign = "right";
                ctx.font = `bold ${scalePx(16)}px monospace`;
                ctx.fillText("Generated by WebGIS AI Agent", targetW - scalePx(36), targetH - scalePx(18));
                ctx.textAlign = "left";
              }

              // ── 6. Upload / convert ───────────────────────────────────────
              const dataUrl = exportCanvas.toDataURL("image/png");
              const res = await fetch(dataUrl);
              const blob = await res.blob();
              const formData = new FormData();
              formData.append("file", blob, "export.png");
              if (title) formData.append("title", title);
              const uploadRes = await fetch(`${API_BASE}/api/v1/export`, { method: "POST", body: formData });
              const data = await uploadRes.json();
              useHudStore.getState().setPendingSystemMessage(
                `[系统通知] 专题地图 \`${title || "未命名"}\` 已成功排版合成，分配URL：${data.url}。`
              );
```

- [ ] **Step 3: Commit**
```bash
git add frontend/components/map/map-action-handler.tsx frontend/components/sidebar/export-layout-tab.tsx
git commit -m "feat(export): wire up export map canvas composer with crop and DPI upscaling"
```

---

**Plan complete and saved to `docs/superpowers/plans/2026-05-14-map-export-layout.md`. Two execution options:**

**1. Subagent-Driven (recommended)** - I dispatch a fresh subagent per task, review between tasks, fast iteration

**2. Inline Execution** - Execute tasks in this session using executing-plans, batch execution with checkpoints

**Which approach?**